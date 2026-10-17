"""本机设备选择与多路启动编排；检测进程仍由 Controller 管理。"""
import copy
import importlib.util
import threading
import uuid
from functools import lru_cache, wraps

import camera_source as source
from catalog import EVENTS, validate_camera, atomic_json

OPERATION_LOCK = threading.RLock()


def serialized_operation(function):
    @wraps(function)
    def call(*args, **kwargs):
        if not OPERATION_LOCK.acquire(blocking=False):
            raise ValueError('另一批摄像头正在接入，请稍后重试')
        try:
            return function(*args, **kwargs)
        finally:
            OPERATION_LOCK.release()
    return call


@lru_cache(maxsize=1)
def stream_manager():
    spec = importlib.util.spec_from_file_location('live_stream_control', source.KSTOOL/'stream_control.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def device_rows(controller, devices):
    """离线设备保留已保存的选择，设备枚举不代表取流成功。"""
    rows = {d.get('id', d['name']): {**d, 'id': d.get('id', d['name']), 'connected': True}
            for d in devices}
    with controller.lock:
        cameras = copy.deepcopy(controller.store.cameras)
    for camera in cameras:
        device_id = camera.get('liveDeviceId')
        if not device_id:
            continue
        row = rows.setdefault(device_id, {'id': device_id,
                                         'name': camera.get('liveDeviceName', device_id),
                                         'connected': False, 'usable': False})
        tasks = [t for t in camera['tasks'] if t['enabled']]
        models = {t['model'] for t in tasks}
        row['selection'] = {'model': next(iter(models)) if len(models) == 1 else '',
                            'events': [t['eventId'] for t in tasks]}
        row.update(cameraId=camera['id'], path=camera['livePath'], url=camera['url'])
    return list(rows.values())


@serialized_operation
def configure(controller, selections, start, models, capacity):
    if type(start) is not bool or not isinstance(selections, list) or not 1 <= len(selections) <= 64:
        raise ValueError('请选择 1～64 台摄像头，启动参数须为布尔值')
    return _configure(controller, selections, start, models, capacity)


def _configure(controller, selections, start, models, capacity):
    stream = controller.live_stream_status()
    if stream.get('active') and stream.get('mode') != 'live':
        raise ValueError('模拟流正在运行，请先通过模拟流停止脚本停止，再接入本机摄像头')
    devices, error = source.list_camera_devices(with_error=True)
    devices = {d.get('id', d['name']): d for d in devices}
    ready = {m['id'] for m in models if m['ready']}
    plans, seen = [], set()
    # 整批先校验。无效模型、场景、重复设备或运行中修改，不留下半批配置。
    with controller.lock, source.CONFIG_LOCK:
        existing = {c.get('liveDeviceId'): c for c in controller.store.cameras if c.get('liveDeviceId')}
        config = source.live_config_path()
        mapping = source.managed_paths(config)
        port = source.config_rtsp_port(config.read_text(encoding='utf-8')) if config.exists() else 8554
        for item in selections:
            if not isinstance(item, dict):
                raise ValueError('摄像头选择格式错误')
            device_id, model, events = item.get('deviceId'), item.get('model'), item.get('events')
            if not isinstance(device_id, str) or not device_id or device_id in seen:
                raise ValueError('摄像头标识为空或重复')
            seen.add(device_id)
            if not isinstance(model, str) or model not in ready:
                raise ValueError('所选模型尚未就绪')
            if not isinstance(events, list) or not events or any(
                    not isinstance(e, str) or e not in EVENTS or EVENTS[e]['model'] != model or
                    EVENTS[e].get('region') for e in events) or len(set(events)) != len(events):
                raise ValueError('请选择同一个模型下的检测场景；区域入侵请在视频配置中绘制区域')
            old = existing.get(device_id)
            device = devices.get(device_id)
            if not device and not old:
                raise ValueError(error or '所选设备已断开，请重新检测')
            if device and not device['usable']:
                raise ValueError(device.get('error') or '设备无法用于视频采集')
            aliases = {device_id}
            if device and device['name'] in mapping.values():
                if sum(d['name'] == device['name'] for d in devices.values()) != 1:
                    raise ValueError('旧接入配置使用了同名设备，请先在“已有 RTSP 映射”移除旧映射再接入')
                aliases.add(device['name'])
            paths = [p for p, d in mapping.items() if d in aliases]
            if len(paths) > 1:
                raise ValueError('该设备存在多个旧映射，请先移除重复映射，避免独占冲突')
            path = paths[0] if paths else (old or {}).get('livePath')
            if old is None and path:
                matches = [c for c in controller.store.cameras if c['url'] == source.live_stream_url(path, port)]
                if len(matches) > 1:
                    raise ValueError('该 RTSP 地址已有多条视频配置，请先保留一条再接入')
                if matches:
                    old = {**matches[0], 'livePath': path}
            tasks = [{**next((t for t in (old or {}).get('tasks', []) if t['eventId'] == e), {}),
                      'eventId': e, 'enabled': True} for e in events]
            unchanged = old and {t['eventId'] for t in old['tasks'] if t['enabled']} == set(events)
            if old and controller.running(old['id']) and not unchanged:
                raise ValueError('请先停止这路分析再修改模型或场景')
            plans.append((device_id, device, old, tasks, path))
        new_count = sum(not old or not controller.running(old['id']) for _, _, old, _, _ in plans)
        if start and controller.running_count() + new_count > capacity:
            raise ValueError('同时运行的视频将超过上限 %d 路，请减少选择或先停止其他视频' % capacity)
        config = source.ensure_live_config()
        names = source.all_path_names(config)
        port = source.config_rtsp_port(config.read_text(encoding='utf-8'))
        saved = []
        for device_id, device, old, tasks, path in plans:
            if not path:
                index = 1
                while 'livecam%d' % index in names:
                    index += 1
                path = 'livecam%d' % index
            names.add(path)
            name = device['name'] if device else old['liveDeviceName']
            camera = {**(old or {}), 'id': old['id'] if old else uuid.uuid4().hex[:12],
                      'name': old['name'] if old else name, 'location': (old or {}).get('location', ''),
                      'url': source.live_stream_url(path, port), 'tasks': tasks,
                      'regions': (old or {}).get('regions', []), 'cover': (old or {}).get('cover', ''),
                      'liveDeviceId': device_id, 'liveDeviceName': name, 'livePath': path}
            camera = validate_camera(camera)
            saved.append((camera, bool(device)))
        before = config.read_text(encoding='utf-8')
        source.write_live_config(config, {c['livePath']: c['liveDeviceId'] for c, _ in saved})
        replacements = {c['id']: c for c, _ in saved}
        updated = [replacements.pop(c['id'], c) for c in controller.store.cameras] + list(replacements.values())
        try:
            atomic_json(controller.store.path, updated)
        except OSError:
            source.atomic_text(config, before)
            raise
        controller.store.cameras = updated
    results = [{'deviceId': c['liveDeviceId'], 'cameraId': c['id'], 'url': c['url'],
                'ok': True, 'status': 'saved', 'message': '配置已保存'} for c, _ in saved]
    if start:
        try:
            if not controller.live_stream_status().get('running'):
                controller.control_live_streams('start')
        except (ValueError, OSError) as exc:
            for result in results:
                result.update(ok=False, status='failed', message=str(exc))
            return {'ok': False, 'results': results}
        for (camera, connected), result in zip(saved, results):
            try:
                if not connected:
                    raise ValueError('设备离线，配置已保留；连接后可重试')
                with controller.lock:
                    running = controller.running(camera['id'])
                if not running:
                    if not source.wait_live_ready(camera['livePath']):
                        raise ValueError('摄像头未在 12 秒内出流，请检查设备占用、连接和支持格式后重试')
                    probe = controller.probe(camera['url'])
                    with controller.lock:
                        current = controller.store.get(camera['id'])
                        if current != camera:
                            raise ValueError('视频配置已被其他操作修改，请刷新后重试')
                        controller.store.save({**current, 'cover': probe['cover']})
                        controller.start(camera['id'])
                result.update(status='starting' if not running else 'running',
                              message='已读到首帧，分析正在启动' if not running else '分析已在运行')
            except (ValueError, OSError, RuntimeError) as exc:
                result.update(ok=False, status='failed', message=str(exc))
    return {'ok': all(r['ok'] for r in results), 'results': results}
