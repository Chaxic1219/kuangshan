"""向本机 events.db 写入演示告警，供告警大屏与顶栏提醒联调。

用法（在 analysis-service 目录）:
  .\\.venv\\Scripts\\python.exe seed_demo_alerts.py
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import uuid
from pathlib import Path

import cv2
import numpy as np

from alert_dashboard import ensure_indexes
from catalog import EVENTS, ROOT

WORK = ROOT / 'data'
DB = WORK / 'events.db'
EVIDENCE = WORK / 'evidence'
SITE_TZ = timezone(timedelta(hours=8))

# 与当前配置中的视频编号对齐；若已删除则大屏仍会显示历史名称。
CAMERAS = [
    ('4d2a94e970c3', 'camera6'),
    ('9e131381289e', 'camera15'),
    ('706bef61f91e', 'USB 摄像头测试'),
]

SCENARIOS = [
    ('no_helmet', '未戴安全帽', [{'label': 'NO-Hardhat', 'confidence': 0.91, 'bbox': [0.2, 0.15, 0.55, 0.7]}]),
    ('no_vest', '未穿反光衣', [{'label': 'NO-Safety Vest', 'confidence': 0.87, 'bbox': [0.25, 0.3, 0.7, 0.9]}]),
    ('fire', '火焰检测', [{'label': 'fire', 'confidence': 0.93, 'bbox': [0.4, 0.35, 0.65, 0.7]}]),
    ('smoke', '烟雾检测', [{'label': 'smoke', 'confidence': 0.82, 'bbox': [0.15, 0.1, 0.8, 0.55]}]),
    ('intrusion', '人员区域入侵', [{'label': 'person', 'confidence': 0.88, 'bbox': [0.3, 0.2, 0.6, 0.85]}]),
    ('brightness', '亮度异常', [{'label': '过暗', 'confidence': 1.0, 'bbox': [0.05, 0.05, 0.95, 0.95]}]),
]


def placeholder_jpeg(path: Path, title: str, color=(36, 72, 48)) -> None:
    canvas = np.full((360, 640, 3), color, dtype=np.uint8)
    cv2.rectangle(canvas, (40, 40), (600, 320), (80, 200, 120), 2)
    cv2.putText(canvas, 'DEMO ALERT', (60, 120), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (220, 240, 230), 2)
    cv2.putText(canvas, title[:40], (60, 180), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 220, 210), 2)
    ok, buf = cv2.imencode('.jpg', canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise OSError('无法生成演示证据图')
    path.write_bytes(buf.tobytes())


def main() -> None:
    WORK.mkdir(exist_ok=True)
    EVIDENCE.mkdir(exist_ok=True)
    ensure_indexes(DB)
    now = datetime.now(timezone.utc)
    rows = []
    # 覆盖今日与近几小时，保证默认「今日」大屏有曲线与分类。
    offsets_hours = [0.2, 0.8, 1.5, 2.5, 4, 6, 8, 10, 12, 14, 16, 18]
    for index, hours in enumerate(offsets_hours):
        camera_id, camera_name = CAMERAS[index % len(CAMERAS)]
        event_id, event_name, detections = SCENARIOS[index % len(SCENARIOS)]
        if event_id not in EVENTS:
            continue
        at = (now - timedelta(hours=hours)).astimezone(SITE_TZ).astimezone(timezone.utc)
        aid = uuid.uuid4().hex
        image = f'/evidence/{aid}.jpg'
        placeholder_jpeg(EVIDENCE / f'{aid}.jpg', f'{event_name} · {camera_name}')
        record = {
            'id': aid,
            'cameraId': camera_id,
            'cameraName': camera_name,
            'eventId': event_id,
            'eventName': event_name,
            'capturedAt': at.isoformat(),
            'image': image,
            'detections': detections,
            'regions': [],
            'alarm': True,
            'schemaVersion': 2,
            'demo': True,
        }
        rows.append((aid, camera_id, at.isoformat(), json.dumps(record, ensure_ascii=False)))

    with closing(sqlite3.connect(DB, timeout=30)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, camera TEXT, at TEXT, record TEXT)')
        # 清掉旧的演示数据，保留真实告警（无 demo 标记）。
        existing = db.execute('SELECT id, record FROM alerts').fetchall()
        removed = 0
        for aid, raw in existing:
            try:
                payload = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if payload.get('demo'):
                db.execute('DELETE FROM alerts WHERE id=?', (aid,))
                (EVIDENCE / f'{aid}.jpg').unlink(missing_ok=True)
                removed += 1
        db.executemany('INSERT INTO alerts VALUES (?,?,?,?)', rows)
        total = db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0]

    print(f'已写入演示告警 {len(rows)} 条（清除旧演示 {removed} 条），库内合计 {total} 条 → {DB}')


if __name__ == '__main__':
    main()
