"""真实加载已登记的三组权重，核对类别并执行一帧推理。"""
import hashlib
import json
import time
import av
from catalog import ROOT, MODEL_CATALOG, EVENTS, MEDIA_ROOT, atomic_json


def main():
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(2)
    manifest = json.loads((ROOT/'weights/manifest.json').read_text(encoding='utf-8'))
    video = MEDIA_ROOT/'mediamtx/videos/camera1.mp4'
    with av.open(str(video)) as container:
        frame = next(container.decode(video=0)).to_ndarray(format='bgr24')
    report = {}
    for key in ('general', 'ppe', 'fire'):
        path = ROOT/'weights'/MODEL_CATALOG[key]['file']
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == manifest[key]['sha256'], '权重文件哈希发生变化'
        model = YOLO(str(path), task='detect')
        names = list(model.names.values())
        required = {name for e in EVENTS.values() if e['model'] == key for name in e['labels']}
        assert required <= {name.lower() for name in names}, f'{key} 缺少要求的训练类别'
        start = time.monotonic()
        result = model.predict(frame, device='cpu', verbose=False, imgsz=640)[0]
        report[key] = {'passed': True, 'sha256': digest, 'classes': names,
                       'inferenceMs': round((time.monotonic()-start)*1000, 1), 'detections': len(result.boxes)}
        print(key, report[key], flush=True)
    atomic_json(ROOT/'weights/verified.json', report)


if __name__ == '__main__':
    main()
