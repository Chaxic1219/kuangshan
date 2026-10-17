"""三组本地 YOLO 权重及 OpenCV 场景适配。"""
import os
os.environ.setdefault('YOLO_AUTOINSTALL', 'false')
os.environ.setdefault('YOLO_VERBOSE', 'false')
import cv2
from stream_infer.algo import BaseAlgo
from catalog import ROOT, EVENTS, MODEL_CATALOG
from regions import contains


class ModelPool:
    def __init__(self, tasks):
        self.models, self.thresholds, self.cache = {}, {}, {}
        if all(t['model'] == 'opencv' for t in tasks):
            return
        import torch
        from ultralytics import YOLO
        torch.set_num_threads(2)
        for task in tasks:
            key = task['model']
            if key == 'opencv':
                continue
            self.thresholds[key] = min(self.thresholds.get(key, 1), task['confidence'])
            if key not in self.models:
                path = ROOT / 'weights' / MODEL_CATALOG[key]['file']
                if not path.is_file():
                    raise ValueError('模型权重不存在，请完成权重安装')
                self.models[key] = YOLO(str(path), task='detect')

    def begin_frame(self):
        self.cache.clear()

    def detect(self, key, frame):
        if key not in self.cache:
            model = self.models[key]
            args = dict(conf=self.thresholds[key], imgsz=640, verbose=False, device='cpu')
            # ByteTrack 保持目标 ID，检测框可随目标在画面中移动。
            result = model.track(frame, persist=True, tracker='bytetrack.yaml', **args)[0]
            rows = []
            for box in result.boxes:
                label = result.names[int(box.cls.item())]
                rows.append({'label': label, 'confidence': float(box.conf.item()),
                             'bbox': [max(0., min(1., float(x))) for x in box.xyxyn[0].tolist()],
                             'trackId': int(box.id.item()) if box.id is not None else None})
            self.cache[key] = rows
        return self.cache[key]


class SceneAlgo(BaseAlgo):
    def __init__(self, task, pool, zones):
        super().__init__(task['id'])
        self.task, self.pool, self.zones = task, pool, zones

    def init(self):
        pass

    def run(self, frames):
        frame = frames[-1]
        event = EVENTS[self.task['eventId']]
        if self.task['model'] == 'opencv':
            mean = float(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean() / 255)
            detections = []
            if mean < self.task['dark'] or mean > self.task['bright']:
                detections = [{'label': '过暗' if mean < self.task['dark'] else '过亮',
                               'confidence': 1., 'brightness': round(mean, 4), 'bbox': [0., 0., 1., 1.]}]
        else:
            wanted = {name.lower() for name in event.get('display', event['labels'])}
            alarm_labels = {name.lower() for name in event['labels']}
            detections = []
            for item in self.pool.detect(self.task['model'], frame):
                label = item['label'].lower()
                if label not in wanted or item['confidence'] < self.task['confidence']:
                    continue
                row = dict(item)
                row['alarmable'] = label in alarm_labels
                detections.append(row)
        # 入侵任务须把区域外目标也交给状态机，以识别离开后再次进入。
        if self.zones and not event.get('region'):
            detections = [x for x in detections if any(contains(z['points'], x['bbox']) for z in self.zones)]
        return {'detections': detections}
