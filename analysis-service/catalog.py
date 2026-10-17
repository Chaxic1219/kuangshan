"""视频、模型与场景目录。"""
import copy
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import urlsplit
import uuid
from regions import validate_polygon

ROOT = Path(__file__).resolve().parent
# 素材根目录：默认项目根下的 kstool（含 mediamtx/videos 与 mediamtx/bin），
# 可用 KUANGSHAN_MEDIA_ROOT 指向其他位置。
MEDIA_ROOT = Path(os.environ.get('KUANGSHAN_MEDIA_ROOT') or (ROOT.parent / 'kstool'))
VIDEO_SUFFIXES = ('.mp4', '.avi', '.mkv', '.mov', '.ts')
MODEL_CATALOG = {
    'general': {'name': '通用目标检测', 'file': 'general.pt', 'architecture': 'YOLOv8n',
                'description': '人员、汽车、卡车、公交车、摩托车、自行车'},
    'ppe': {'name': '安全装备检测', 'file': 'ppe.pt', 'architecture': 'YOLO11n',
            'description': '安全帽、反光衣及未佩戴类别'},
    'fire': {'name': '火焰与烟雾检测', 'file': 'fire.pt', 'architecture': 'YOLOv8n',
             'description': '火焰、烟雾'},
    'opencv': {'name': '亮度检测', 'file': None, 'architecture': 'OpenCV',
               'description': '灰度均值判断过暗或过亮'},
}
EVENTS = {
    'person': {'name': '人员检测', 'model': 'general', 'labels': ['person'], 'alarm': False},
    'vehicle': {'name': '车辆检测', 'model': 'general', 'labels': ['car', 'truck', 'bus', 'motorcycle', 'bicycle'], 'alarm': False},
    'intrusion': {'name': '人员区域入侵', 'model': 'general', 'labels': ['person'], 'alarm': True, 'region': True},
    'vehicle_intrusion': {'name': '车辆区域入侵', 'model': 'general', 'labels': ['car', 'truck', 'bus', 'motorcycle', 'bicycle'], 'alarm': True, 'region': True},
    # display：画面上跟随绘制的类别；labels：触发报警的类别。不把人员画进装备检查场景。
    'no_helmet': {'name': '未戴安全帽', 'model': 'ppe', 'labels': ['no-hardhat'],
                  'display': ['no-hardhat', 'hardhat'], 'alarm': True},
    'no_vest': {'name': '未穿反光衣', 'model': 'ppe', 'labels': ['no-safety vest'],
                'display': ['no-safety vest', 'safety vest'], 'alarm': True},
    'fire': {'name': '火焰检测', 'model': 'fire', 'labels': ['fire'], 'alarm': True},
    'smoke': {'name': '烟雾检测', 'model': 'fire', 'labels': ['smoke'], 'alarm': True},
    'brightness': {'name': '亮度异常', 'model': 'opencv', 'labels': [], 'alarm': True},
}


def number(value, lo, hi):
    return type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi


def identifier(value):
    return isinstance(value, str) and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value))


def resolve_source(url):
    """把本地视频地址解析为绝对路径；网络流地址原样返回。"""
    text = str(url)
    path = Path(text)
    if path.is_absolute():
        return path
    candidate = ROOT.parent / path
    return candidate if candidate.exists() else text


def validate_source(url):
    if not isinstance(url, str) or not url.strip() or len(url) > 2048:
        raise ValueError('请填写有效视频地址')
    text = url.strip()
    path = Path(text)
    absolute = (path if path.is_absolute() else ROOT.parent / path).resolve()
    if absolute.is_file() and absolute.suffix.lower() in VIDEO_SUFFIXES:
        # 项目内的本地视频存为相对路径，迁移项目目录后仍然有效。
        try:
            return absolute.relative_to(ROOT.parent.resolve()).as_posix()
        except ValueError:
            return str(absolute)
    try:
        parsed = urlsplit(text)
        if parsed.scheme not in ('rtsp', 'rtsps', 'rtmp', 'rtmps', 'http', 'https') or not parsed.hostname or \
                (parsed.port is not None and not 1 <= parsed.port <= 65535):
            raise ValueError()
    except (ValueError, OSError):
        raise ValueError('地址须为 RTSP、RTMP、HTTP(S) 视频流或已存在的本地视频文件') from None
    return url


def validate_camera(data):
    if not isinstance(data, dict):
        raise ValueError('视频配置格式错误')
    result = copy.deepcopy(data)
    if not identifier(result.get('id')):
        raise ValueError('视频编号不合法')
    for key, limit in [('name', 100), ('location', 200)]:
        value = result.get(key, '')
        if not isinstance(value, str) or len(value) > limit or (key == 'name' and not value.strip()):
            raise ValueError('请填写有效名称和位置')
        result[key] = value.strip()
    result['url'] = validate_source(result.get('url'))
    zones = result.get('regions', [])
    tasks = result.get('tasks', [])
    if not isinstance(zones, list) or len(zones) > 32 or not isinstance(tasks, list):
        raise ValueError('任务或区域格式错误；每路最多 32 个区域')
    ids = set()
    for zone in zones:
        if not isinstance(zone, dict) or not identifier(zone.get('id')) or zone['id'] in ids:
            raise ValueError('区域编号不合法或重复')
        if not isinstance(zone.get('name'), str) or not 1 <= len(zone['name'].strip()) <= 80:
            raise ValueError('请填写区域名称')
        if type(zone.get('enabled', True)) is not bool:
            raise ValueError('区域启用状态格式错误')
        validate_polygon(zone.get('points'))
        ids.add(zone['id'])
    seen = set()
    for task in tasks:
        if not isinstance(task, dict) or task.get('eventId') not in EVENTS or task['eventId'] in seen:
            raise ValueError('任务未知或重复')
        eid = task['eventId']
        seen.add(eid)
        for key, default, lo, hi in [('confidence', .4, .05, .99), ('interval', 0, 0, 60),
                                      ('cooldown', 30, 0, 3600), ('dark', .12, 0, 1), ('bright', .88, 0, 1)]:
            task.setdefault(key, default)
            if not number(task[key], lo, hi):
                raise ValueError('置信度、间隔或亮度阈值超出有效范围')
        if task['dark'] >= task['bright']:
            raise ValueError('过暗阈值必须小于过亮阈值')
        task.setdefault('enabled', True)
        if type(task['enabled']) is not bool:
            raise ValueError('任务启用状态格式错误')
        selected = task.setdefault('regionIds', [])
        if not isinstance(selected, list) or any(not isinstance(x, str) or x not in ids for x in selected) or len(selected) != len(set(selected)):
            raise ValueError('任务绑定了不存在或重复的区域')
        if EVENTS[eid].get('region') and task['enabled'] and not any(
                z['id'] in selected and z.get('enabled', True) for z in zones):
            raise ValueError('区域入侵任务至少需要一个已启用的布控区域')
        task['model'] = EVENTS[eid]['model']
        task['id'] = eid
    result['tasks'], result['regions'] = tasks, zones
    return result


def atomic_json(path, data):
    path = Path(path)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
    temp.replace(path)


def model_status():
    report = ROOT / 'weights/verified.json'
    verified = json.loads(report.read_text(encoding='utf-8')) if report.exists() else {}
    result = []
    for key, info in MODEL_CATALOG.items():
        evidence = verified.get(key, {})
        ready = key == 'opencv' or (evidence.get('passed') is True and (ROOT / 'weights' / info['file']).is_file())
        result.append({'id': key, **info, 'ready': ready, 'classes': evidence.get('classes', []),
                       'message': '已验证本地推理' if ready else '权重尚未验证'})
    return result


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'cameras.json'
        self.cameras = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else []
        for camera in self.cameras:
            validate_camera(camera)

    def get(self, cid):
        return next((copy.deepcopy(x) for x in self.cameras if x['id'] == cid), None)

    def save(self, data):
        data = copy.deepcopy(data)
        data['id'] = data.get('id') or uuid.uuid4().hex[:12]
        value = validate_camera(data)
        updated = [x for x in self.cameras if x['id'] != value['id']] + [value]
        atomic_json(self.path, updated)
        self.cameras = updated
        return value

    def delete(self, cid):
        updated = [x for x in self.cameras if x['id'] != cid]
        atomic_json(self.path, updated)
        self.cameras = updated
