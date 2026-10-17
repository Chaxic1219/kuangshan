"""离线准确率测量工具：在带标注样本上统计人员/装备/烟火的漏报与误报。

边界（与设计一致）：
- 没有业务阈值和现场标注集时只报告测量结果，不判定“准确率是否达标”；
- 输出固定包含 assessment=measurement_only，accuracy 字段不参与结论；
- 标注格式：JSONL，每行 {"image": "xxx.jpg", "boxes": [{"label": "...", "bbox": [x1,y1,x2,y2]}]}，
  bbox 为归一化 xyxy（0~1），label 使用模型类别名；
- 匹配规则：同标签、IoU>=阈值、一对一贪心，先匹配高置信度预测。

用法：
  python evaluate_accuracy.py template --video videos/camera5.mp4 --out dataset --count 30 --step 30
  python evaluate_accuracy.py evaluate --model ppe --images dataset/images --annotations dataset/labels.jsonl
  python evaluate_accuracy.py measure --model general --images dataset/images
"""
import argparse
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from catalog import ROOT, MODEL_CATALOG

GROUPS = {
    'person': {'model': 'general', 'labels': ['person']},
    'ppe': {'model': 'ppe',
            'labels': ['hardhat', 'no-hardhat', 'safety vest', 'no-safety vest', 'person',
                       'mask', 'no-mask']},
    'fire': {'model': 'fire', 'labels': ['fire', 'smoke']},
}
IMAGE_SUFFIXES = {'.jpg', '.jpeg', '.png'}


def now_tag():
    return datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')


def iou(a, b):
    x1 = max(float(a[0]), float(b[0]))
    y1 = max(float(a[1]), float(b[1]))
    x2 = min(float(a[2]), float(b[2]))
    y2 = min(float(a[3]), float(b[3]))
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0:
        return 0.0
    area_a = max(0.0, float(a[2]) - float(a[0])) * max(0.0, float(a[3]) - float(a[1]))
    area_b = max(0.0, float(b[2]) - float(b[0])) * max(0.0, float(b[3]) - float(b[1]))
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def match_boxes(ground_truth, predictions, iou_threshold=0.5):
    """同标签一对一贪心匹配；返回 (匹配列表, 已匹配 GT 下标集合)。

    匹配列表元素为 (gt_index 或 None, prediction)；None 表示误报。
    """
    used = set()
    matched = []
    for prediction in sorted(predictions, key=lambda item: item.get('confidence', 1.0), reverse=True):
        best_index, best_iou = None, 0.0
        for index, truth in enumerate(ground_truth):
            if index in used:
                continue
            if str(truth.get('label', '')).lower() != str(prediction.get('label', '')).lower():
                continue
            value = iou(truth['bbox'], prediction['bbox'])
            if value > best_iou:
                best_index, best_iou = index, value
        if best_index is not None and best_iou >= iou_threshold:
            used.add(best_index)
            matched.append((best_index, prediction))
        else:
            matched.append((None, prediction))
    return matched, used


def compute_metrics(annotations, predictions, iou_threshold=0.5):
    """按标签统计 TP/FP/FN 与 precision/recall；输入为逐图标注与预测列表。"""
    stats = defaultdict(lambda: {'tp': 0, 'fp': 0, 'fn': 0, 'annotations': 0, 'predictions': 0})
    for truth_boxes, pred_boxes in zip(annotations, predictions):
        for truth in truth_boxes:
            stats[str(truth['label'])]['annotations'] += 1
        for prediction in pred_boxes:
            stats[str(prediction['label'])]['predictions'] += 1
        matched, used = match_boxes(truth_boxes, pred_boxes, iou_threshold)
        for index, prediction in matched:
            if index is None:
                stats[str(prediction['label'])]['fp'] += 1
            else:
                stats[str(prediction['label'])]['tp'] += 1
        for index, truth in enumerate(truth_boxes):
            if index not in used:
                stats[str(truth['label'])]['fn'] += 1
    result = {}
    for label, item in sorted(stats.items()):
        tp, fp, fn = item['tp'], item['fp'], item['fn']
        item['precision'] = round(tp / (tp + fp), 4) if tp + fp else None
        item['recall'] = round(tp / (tp + fn), 4) if tp + fn else None
        if item['precision'] is not None and item['recall'] is not None \
                and item['precision'] + item['recall'] > 0:
            item['f1'] = round(2 * item['precision'] * item['recall']
                               / (item['precision'] + item['recall']), 4)
        else:
            item['f1'] = None
        result[label] = item
    return result


def overall_from_labels(labels):
    tp = sum(item['tp'] for item in labels.values())
    fp = sum(item['fp'] for item in labels.values())
    fn = sum(item['fn'] for item in labels.values())
    precision = round(tp / (tp + fp), 4) if tp + fp else None
    recall = round(tp / (tp + fn), 4) if tp + fn else None
    f1 = None
    if precision is not None and recall is not None and precision + recall > 0:
        f1 = round(2 * precision * recall / (precision + recall), 4)
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': precision, 'recall': recall, 'f1': f1}


def load_annotations(path):
    """读取 JSONL 标注；返回 {image_name: [boxes]}，非法行直接报错。"""
    annotations = {}
    for line_number, line in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            raise ValueError('标注第 %d 行不是合法 JSON' % line_number) from None
        image = item.get('image')
        if not isinstance(image, str) or not image:
            raise ValueError('标注第 %d 行缺少 image' % line_number)
        boxes = item.get('boxes', [])
        if not isinstance(boxes, list):
            raise ValueError('标注第 %d 行 boxes 必须是数组' % line_number)
        checked = []
        for box in boxes:
            if not isinstance(box, dict) or not isinstance(box.get('label'), str) or not box['label']:
                raise ValueError('标注第 %d 行存在缺少 label 的框' % line_number)
            bbox = box.get('bbox')
            if not isinstance(bbox, list) or len(bbox) != 4 or not all(
                    isinstance(value, (int, float)) and math.isfinite(value) and 0 <= value <= 1
                    for value in bbox):
                raise ValueError('标注第 %d 行 bbox 必须是 0~1 的归一化坐标' % line_number)
            if not (bbox[0] < bbox[2] and bbox[1] < bbox[3]):
                raise ValueError('标注第 %d 行 bbox 坐标顺序错误' % line_number)
            checked.append({'label': box['label'], 'bbox': [float(value) for value in bbox]})
        annotations[image] = checked
    return annotations


def images_in(directory):
    directory = Path(directory)
    return sorted(path for path in directory.iterdir()
                  if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES)


def load_model(model_key):
    import torch
    from ultralytics import YOLO
    torch.set_num_threads(2)
    path = ROOT/'weights'/MODEL_CATALOG[model_key]['file']
    if not path.is_file():
        raise ValueError('模型权重不存在：%s' % path)
    return YOLO(str(path), task='detect')


def predict(model, image_path, confidence):
    import cv2
    frame = cv2.imread(str(image_path))
    if frame is None:
        raise ValueError('无法读取图片：%s' % image_path)
    result = model.predict(frame, conf=confidence, imgsz=640, verbose=False, device='cpu')[0]
    rows = []
    for box in result.boxes:
        label = result.names[int(box.cls.item())]
        rows.append({'label': label, 'confidence': float(box.conf.item()),
                     'bbox': [max(0.0, min(1.0, float(value))) for value in box.xyxyn[0].tolist()]})
    return rows


def evaluate(model_key, images_dir, annotations_path, confidence, iou_threshold, output_dir):
    annotations = load_annotations(annotations_path)
    images = [path for path in images_in(images_dir) if path.name in annotations]
    if not images:
        raise ValueError('标注与图片没有交集，无法评估')
    model = load_model(model_key)
    truth_per_image, pred_per_image = [], []
    for image in images:
        truth_per_image.append(annotations[image.name])
        pred_per_image.append(predict(model, image, confidence))
    labels = compute_metrics(truth_per_image, pred_per_image, iou_threshold)
    report = {
        'assessment': 'measurement_only',
        'note': '未设置业务阈值与现场标注集时只报告测量结果，不判定准确率是否达标。',
        'model': model_key,
        'confidenceThreshold': confidence,
        'iouThreshold': iou_threshold,
        'images': len(images),
        'annotatedImages': len(annotations),
        'overall': overall_from_labels(labels),
        'labels': labels,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir/'accuracy.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return path, report


def measure(model_key, images_dir, confidence, output_dir):
    images = images_in(images_dir)
    if not images:
        raise ValueError('图片目录为空，无法测量')
    model = load_model(model_key)
    counts = defaultdict(int)
    confidences = defaultdict(list)
    started = time.monotonic()
    for image in images:
        for row in predict(model, image, confidence):
            counts[row['label']] += 1
            confidences[row['label']].append(row['confidence'])
    elapsed = round(time.monotonic() - started, 2)
    report = {
        'assessment': 'measurement_only',
        'note': '没有标注集，只能报告检出数量，不能计算漏报/误报或准确率。',
        'model': model_key,
        'confidenceThreshold': confidence,
        'images': len(images),
        'detections': {label: counts[label] for label in sorted(counts)},
        'meanConfidence': {label: round(sum(values) / len(values), 4)
                           for label, values in sorted(confidences.items())},
        'elapsedSeconds': elapsed,
        'generatedAt': datetime.now(timezone.utc).isoformat(),
    }
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir/'measurement.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return path, report


def template(video, output_dir, count, step):
    """从视频抽帧并生成空标注模板，供现场人工标注。"""
    import av
    import cv2
    output_dir = Path(output_dir)
    images = output_dir/'images'
    images.mkdir(parents=True, exist_ok=True)
    lines = []
    with av.open(str(video)) as container:
        stream = container.streams.video[0]
        index = 0
        taken = 0
        for frame in container.decode(stream):
            if taken >= count:
                break
            if index % max(1, step) == 0:
                array = frame.to_ndarray(format='bgr24')
                name = 'frame_%05d.jpg' % taken
                ok, encoded = cv2.imencode('.jpg', array, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                if ok:
                    (images/name).write_bytes(encoded.tobytes())
                    lines.append(json.dumps({'image': name, 'boxes': []}, ensure_ascii=False))
                    taken += 1
            index += 1
    (output_dir/'labels.jsonl').write_text('\n'.join(lines) + ('\n' if lines else ''),
                                           encoding='utf-8')
    (output_dir/'README.md').write_text(
        '# 标注说明\n\n'
        '1. 在 images/ 中逐张标注，labels.jsonl 每行对应一张图片。\n'
        '2. label 使用模型类别名（person / NO-Hardhat / Safety Vest / fire / smoke 等）。\n'
        '3. bbox 为归一化 xyxy：x1,y1,x2,y2 均在 0~1。\n'
        '4. 无目标的图片保留 "boxes": []。\n\n'
        '标注完成后运行：\n'
        '```\npython evaluate_accuracy.py evaluate --model ppe '
        '--images %s/images --annotations %s/labels.jsonl\n```\n' % (output_dir, output_dir),
        encoding='utf-8')
    return output_dir, len(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description='离线准确率测量（只报告测量结果）')
    sub = parser.add_subparsers(dest='command', required=True)

    tpl = sub.add_parser('template', help='从视频抽帧生成标注模板')
    tpl.add_argument('--video', type=Path, required=True)
    tpl.add_argument('--out', type=Path, required=True)
    tpl.add_argument('--count', type=int, default=30)
    tpl.add_argument('--step', type=int, default=30)

    ev = sub.add_parser('evaluate', help='带标注评估漏报/误报')
    ev.add_argument('--model', choices=sorted(MODEL_CATALOG), default='general')
    ev.add_argument('--images', type=Path, required=True)
    ev.add_argument('--annotations', type=Path, required=True)
    ev.add_argument('--conf', type=float, default=0.4)
    ev.add_argument('--iou', type=float, default=0.5)
    ev.add_argument('--out', type=Path)

    me = sub.add_parser('measure', help='无标注，仅统计检出数量')
    me.add_argument('--model', choices=sorted(MODEL_CATALOG), default='general')
    me.add_argument('--images', type=Path, required=True)
    me.add_argument('--conf', type=float, default=0.4)
    me.add_argument('--out', type=Path)

    args = parser.parse_args(argv)
    if args.command == 'template':
        out, count = template(args.video, args.out, args.count, args.step)
        print('已生成标注模板：%s（%d 帧）' % (out, count))
        return 0
    output = args.out or (ROOT/'runs'/('accuracy-' + now_tag()))
    if args.command == 'evaluate':
        path, report = evaluate(args.model, args.images, args.annotations,
                                args.conf, args.iou, output)
        print('评估报告：%s' % path)
        print(json.dumps({'assessment': report['assessment'], 'overall': report['overall'],
                          'images': report['images']}, ensure_ascii=False, indent=2))
    else:
        path, report = measure(args.model, args.images, args.conf, output)
        print('测量报告：%s' % path)
        print(json.dumps({'assessment': report['assessment'],
                          'detections': report['detections']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
