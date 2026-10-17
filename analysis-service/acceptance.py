"""联合验收脚本：短时或长时运行，采样结果年龄 P95、生命周期、资源与告警状态。

边界：
- 使用临时数据目录，不覆盖 data/events.db 或现有 outbox；
- 性能指标只记录测量与配置，不把硬件相关阈值当作硬通过条件；
- 默认尝试以隔离端口启动 2 路模拟流（MediaMTX）并接入 4 路分析；失败可 --no-streams；
- 报告写入 runs/acceptance-<时间>/，含人读 md 与机器读 json。
"""
import argparse
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
PYTHON = sys.executable
KSTOOL = PROJECT/'kstool/mediamtx'
STREAM_CONTROL = KSTOOL/'stream_control.py'
LITE_CONFIG = KSTOOL/'mediamtx-lite.yml'
VIDEOS = KSTOOL/'videos'
LOCAL_CLIPS = [VIDEOS/'camera5.mp4', VIDEOS/'camera12.mp4',
               VIDEOS/'camera13.mp4', VIDEOS/'camera15.mp4']


def now_tag():
    return datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def free_tcp_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def port_open(port):
    with socket.socket() as sock:
        sock.settimeout(0.4)
        return sock.connect_ex(('127.0.0.1', port)) == 0


def wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.2)
    return predicate()


def pick_port(used):
    for port in range(20000, 30000):
        if port in used:
            continue
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                continue
        used.add(port)
        return port
    raise RuntimeError('没有可用端口')


def set_port(text, key, value):
    import re
    pattern = re.compile(r'(?m)^(\s*%s:\s*)\S+\s*$' % re.escape(key))
    return pattern.sub(lambda match: match.group(1) + value, text)


def isolated_stream_config(folder):
    """只替换 lite 配置中实际存在的监听地址，避免给 MediaMTX 引入未知键。"""
    text = LITE_CONFIG.read_text(encoding='utf-8')
    used = set()
    ports = {name: pick_port(used) for name in ('rtsp', 'hls', 'webrtc', 'api')}
    values = {
        'rtspAddress': '127.0.0.1:%d' % ports['rtsp'],
        'hlsAddress': '127.0.0.1:%d' % ports['hls'],
        'webrtcAddress': '127.0.0.1:%d' % ports['webrtc'],
        'apiAddress': '127.0.0.1:%d' % ports['api'],
    }
    for key, value in values.items():
        text = set_port(text, key, value)
    # runOnInit 里的 ffmpeg 推流地址硬编码为默认端口，必须同步替换。
    for prefix in ('rtsp://localhost:8554/', 'rtsp://127.0.0.1:8554/'):
        text = text.replace(prefix, 'rtsp://127.0.0.1:%d/' % ports['rtsp'])
    path = Path(folder)/'streams.yml'
    path.write_text(text, encoding='utf-8')
    return path, ports['rtsp']


def start_streams(folder):
    config, rtsp_port = isolated_stream_config(folder)
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    result = subprocess.run(
        [PYTHON, str(STREAM_CONTROL), 'start', '--mode', 'lite', '--config', str(config)],
        cwd=str(KSTOOL), capture_output=True, text=True, encoding='utf-8',
        errors='replace', env=env, timeout=120)
    if result.returncode != 0:
        raise RuntimeError('模拟流启动失败：%s' % (result.stdout + result.stderr).strip())
    if not wait_until(lambda: port_open(rtsp_port), 20):
        raise RuntimeError('模拟流 RTSP 端口未监听')
    # ffmpeg 推流比端口监听稍慢，给首次连接留出缓冲时间。
    time.sleep(3)
    return rtsp_port


def stop_streams():
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    subprocess.run([PYTHON, str(STREAM_CONTROL), 'stop', '--force'],
                   cwd=str(KSTOOL), capture_output=True, text=True, encoding='utf-8',
                   errors='replace', env=env, timeout=90)


def start_console(work, port):
    return subprocess.Popen(
        [PYTHON, str(ROOT/'console.py'), '--port', str(port), '--data', str(work)],
        cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL)


def http_json(base, path, method='GET', body=None, token=None, timeout=30):
    headers = {'Host': base.split('://', 1)[1]}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers['Content-Type'] = 'application/json'
    if token:
        headers['X-Console-Token'] = token
    request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def add_camera(base, token, name, url):
    probe = http_json(base, '/api/cameras/probe', 'POST', {'url': url}, token)
    saved = http_json(base, '/api/cameras/save', 'POST', {
        'id': '', 'name': name, 'url': url,
        'tasks': [{'eventId': 'person', 'confidence': 0.4, 'interval': 1,
                   'cooldown': 30, 'enabled': True, 'regionIds': []}],
        'regions': [], 'coverToken': probe['coverToken']}, token)
    camera = saved.get('camera', saved)
    return camera['id']


def percentile(values, ratio):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(ratio * len(ordered)) - 1))
    return round(ordered[index], 3)


def summarize_result_ages(samples):
    """按路与整体统计结果年龄，避免合并 P95 掩盖单路性能问题。"""
    per_camera = {}
    all_ages = []
    for sample in samples:
        for camera_id, ages in (sample.get('agesByCamera') or {}).items():
            per_camera.setdefault(camera_id, []).extend(ages)
            all_ages.extend(ages)

    def stats(values):
        return {'samples': len(values), 'p50': percentile(values, 0.5),
                'p95': percentile(values, 0.95),
                'max': round(max(values), 3) if values else None}

    return {'overall': stats(all_ages),
            'perCamera': {cid: stats(values) for cid, values in sorted(per_camera.items())}}


def summarize_camera_ages(samples, camera_ids):
    """逐路结果年龄（包含未产出结果的相机），避免总体 P95 掩盖单路问题。"""
    per_camera = {cid: [] for cid in camera_ids}
    for sample in samples:
        for camera_id, ages in (sample.get('agesByCamera') or {}).items():
            per_camera.setdefault(camera_id, []).extend(ages)
    result = {}
    for camera_id, ages in per_camera.items():
        p95 = percentile(ages, 0.95)
        result[camera_id] = {
            'samples': len(ages),
            'p50': percentile(ages, 0.5),
            'p95': p95,
            'max': round(max(ages), 3) if ages else None,
            'within3s': p95 is not None and p95 <= 3,
        }
    return result


def collect_events(work):
    errors = []
    for path in Path(work).rglob('events.jsonl'):
        try:
            for line in path.read_text(encoding='utf-8').splitlines():
                try:
                    item = json.loads(line)
                except ValueError:
                    continue
                if item.get('type') in ('worker_error', 'model_error', 'alert_rejected'):
                    errors.append({'camera': item.get('cameraId'), 'type': item.get('type'),
                                   'reason': item.get('reason', '')})
        except OSError:
            continue
    return errors


def run(args):
    work = Path(tempfile.mkdtemp(prefix='acceptance-'))
    report_dir = args.out or (ROOT/'runs'/('acceptance-' + now_tag()))
    report_dir.mkdir(parents=True, exist_ok=True)
    console_port = free_tcp_port()
    console = None
    streams_started = False
    samples = []
    started = time.time()
    try:
        rtsp_port = None
        if not args.no_streams:
            try:
                rtsp_port = start_streams(work)
                streams_started = True
            except Exception as exc:
                print('模拟流启动失败，继续本地视频验收：%s' % exc)
        console = start_console(work, console_port)
        base = 'http://127.0.0.1:%d' % console_port
        if not wait_until(lambda: _state_ready(base), 30):
            raise RuntimeError('控制台未就绪')
        state = http_json(base, '/api/state')
        token = state['token']

        local = [path for path in LOCAL_CLIPS if path.is_file()][:max(0, args.cameras - 2)]
        feeds = []
        if rtsp_port:
            feeds += [('stream-camera1', 'rtsp://127.0.0.1:%d/camera1' % rtsp_port),
                      ('stream-camera2', 'rtsp://127.0.0.1:%d/camera2' % rtsp_port)]
        feeds += [(path.stem, str(path)) for path in local]
        feeds = feeds[:args.cameras]
        ids = []
        for name, url in feeds:
            try:
                ids.append(add_camera(base, token, name, url))
            except Exception as exc:
                print('添加视频失败 %s：%s' % (name, exc))
        if len(ids) < args.analyze:
            raise RuntimeError('可用视频不足：期望至少 %d 路，实际 %d 路' % (args.analyze, len(ids)))
        for cid in ids[:args.analyze]:
            http_json(base, '/api/cameras/start', 'POST', {'id': cid}, token)

        deadline = time.monotonic() + args.minutes * 60
        while time.monotonic() < deadline:
            time.sleep(args.interval)
            state = http_json(base, '/api/state')
            ages_by_camera = {}
            lifecycles = {}
            for camera in state['cameras']:
                lifecycles[camera['id']] = camera.get('lifecycle')
                ages = ages_by_camera.setdefault(camera['id'], [])
                for record in (camera.get('latest') or {}).values():
                    captured = record.get('capturedAt')
                    if captured:
                        try:
                            stamp = datetime.fromisoformat(captured).timestamp()
                            ages.append(max(0.0, time.time() - stamp))
                        except (TypeError, ValueError):
                            continue
            samples.append({
                'at': iso_now(),
                'agesByCamera': ages_by_camera,
                'lifecycles': lifecycles,
                'capacity': state.get('capacity'),
                'bridge': state.get('alertBridge'),
                'modelErrors': {camera['id']: camera.get('modelErrors')
                                for camera in state['cameras'] if camera.get('modelErrors')},
            })

        summary = summarize_result_ages(samples)
        overall = summary['overall']
        per_camera = summarize_camera_ages(samples, ids)
        p95 = overall['p95']
        final_state = http_json(base, '/api/state')
        capacity = final_state.get('capacity') or {}
        bridge = final_state.get('alertBridge') or {}
        plan = http_json(base, '/api/maintenance/plan')
        errors = collect_events(work)
        lifecycles = {}
        for camera in final_state['cameras']:
            lifecycles[camera.get('lifecycle')] = lifecycles.get(camera.get('lifecycle'), 0) + 1
        report = {
            'startedAt': started,
            'finishedAt': time.time(),
            'minutes': args.minutes,
            'config': {
                'camerasConfigured': len(ids),
                'camerasAnalyzed': args.analyze,
                'localClips': [path.name for path in local],
                'streams': bool(rtsp_port),
                'tasks': ['person'], 'interval': 1, 'confidence': 0.4,
            },
            'samples': len(samples),
            'resultAgeSeconds': overall,
            'resultAgeSecondsByCamera': per_camera,
            'lifecycles': lifecycles,
            'modelErrors': {camera['id']: camera.get('modelErrors')
                            for camera in final_state['cameras'] if camera.get('modelErrors')},
            'alertBridge': bridge,
            'capacity': {'maxRunning': capacity.get('maxRunning'),
                         'running': capacity.get('running'),
                         'sampledRunning': capacity.get('sampledRunning'),
                         'cpuPercent': capacity.get('cpuPercent'),
                         'rssMb': capacity.get('rssMb'),
                         'storage': capacity.get('storage')},
            'maintenancePreview': {'total': plan.get('total'), 'errors': plan.get('errors'),
                                   'unknown': len(plan.get('unknown') or [])},
            'eventErrors': errors,
            'observations': {
                'resultAgeP95Within3s': (p95 is not None and p95 <= 3),
                'allAnalyzedActive': lifecycles.get('active') == args.analyze,
                'streamsUsed': bool(rtsp_port),
                'camerasOver3s': [cid for cid, item in per_camera.items()
                                  if not item.get('within3s')],
            },
            'limitations': [
                '性能指标为测量值，不代表所有硬件/素材组合；P95 超过 3 秒时应降低并发或增大任务间隔并记录配置。',
                '短时验收不覆盖 8 小时稳定性；长时验收请以 --minutes 480 在固定机器执行。',
                '无现场标注集时未计算准确率（见 evaluate_accuracy.py）。',
            ],
        }
        (report_dir/'report.json').write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        write_markdown(report, report_dir/'report.md')
        print('验收报告：%s' % (report_dir/'report.json'))
        print(json.dumps({k: report[k] for k in ('samples', 'resultAgeSeconds',
                                                 'lifecycles', 'observations')},
                         ensure_ascii=False, indent=2))
        return 0
    finally:
        if console is not None:
            try:
                if 'base' in locals():
                    state = http_json(base, '/api/state', timeout=10)
                    token = state['token']
                    for camera in state['cameras']:
                        if camera.get('running'):
                            try:
                                http_json(base, '/api/cameras/stop', 'POST',
                                          {'id': camera['id']}, token)
                            except Exception:
                                pass
            except Exception:
                pass
            console.terminate()
            try:
                console.wait(timeout=20)
            except subprocess.TimeoutExpired:
                console.kill()
        if streams_started:
            stop_streams()
        if not args.keep_data:
            shutil.rmtree(work, ignore_errors=True)


def _state_ready(base):
    try:
        http_json(base, '/api/state', timeout=3)
        return True
    except Exception:
        return False


def write_markdown(report, path):
    config = report['config']
    lines = [
        '# 联合验收报告', '',
        '- 时间：%s' % datetime.fromtimestamp(report['startedAt'], timezone.utc).isoformat(),
        '- 时长：%.1f 分钟' % report['minutes'],
        '- 配置：%d 路视频、%d 路分析、模拟流=%s' % (
            config['camerasConfigured'], config['camerasAnalyzed'], config['streams']),
        '', '## 测量结果', '',
        '- 结果年龄总体 P50/P95/Max：%s / %s / %s 秒' % (
            report['resultAgeSeconds']['p50'], report['resultAgeSeconds']['p95'],
            report['resultAgeSeconds']['max']),
        '', '### 逐路结果年龄', '',
        '| 视频 | P50 | P95 | Max | 样本 | 3秒内 |', '|---|---|---|---|---|---|',
    ]
    for camera_id, item in sorted((report.get('resultAgeSecondsByCamera') or {}).items()):
        lines.append('| %s | %s | %s | %s | %s | %s |' % (
            camera_id, item.get('p50'), item.get('p95'), item.get('max'),
            item.get('samples'), item.get('within3s')))
    lines += [
        '', '- 生命周期分布：%s' % report['lifecycles'],
        '- 模型错误：%s' % report['modelErrors'],
        '- 告警桥：%s' % report['alertBridge'].get('detail'),
        '- 资源：CPU %s%%，内存 %s MB，磁盘 %s' % (
            report['capacity'].get('cpuPercent'), report['capacity'].get('rssMb'),
            (report['capacity'].get('storage') or {}).get('level')),
        '- 事件错误：%s' % (report['eventErrors'] or '无'),
        '', '## 观察项（非硬性合格判定）', '',
        '- P95 是否在 3 秒内：%s' % report['observations']['resultAgeP95Within3s'],
        '- 全部分析路是否 active：%s' % report['observations']['allAnalyzedActive'],
        '', '## 限制', '',
    ]
    lines += ['- %s' % item for item in report['limitations']]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description='联合验收（短时/长时）')
    parser.add_argument('--minutes', type=float, default=2)
    parser.add_argument('--cameras', type=int, default=6)
    parser.add_argument('--analyze', type=int, default=6)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--no-streams', action='store_true')
    parser.add_argument('--keep-data', action='store_true')
    parser.add_argument('--out', type=Path)
    args = parser.parse_args(argv)
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
