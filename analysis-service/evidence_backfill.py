"""为仅有 jpg、缺少 mp4 的历史告警补静止短视频线索。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import cv2

from evidence_clip import write_evidence_clip


def _evidence_name(url: str | None) -> str | None:
    if not isinstance(url, str) or not url.startswith('/evidence/'):
        return None
    name = url[len('/evidence/'):].strip()
    if not name or '/' in name or '\\' in name or '..' in name:
        return None
    return name


def backfill_missing_videos(
    database: Path | str,
    evidence_dir: Path | str,
    *,
    limit: int = 0,
) -> dict:
    """对有 image 无 video 的告警：用证据 jpg 复制多帧写成短 mp4，并写回 record.video。"""
    db_path = Path(database)
    folder = Path(evidence_dir)
    folder.mkdir(parents=True, exist_ok=True)
    scanned = 0
    created = 0
    skipped = 0
    failed = 0
    with closing(sqlite3.connect(db_path, timeout=60)) as db, db:
        rows = db.execute('SELECT id, record FROM alerts').fetchall()
        for alert_id, raw in rows:
            if limit and created >= limit:
                break
            scanned += 1
            try:
                record = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                failed += 1
                continue
            if not isinstance(record, dict):
                failed += 1
                continue
            if record.get('video'):
                skipped += 1
                continue
            image_name = _evidence_name(record.get('image'))
            if not image_name or not image_name.lower().endswith(('.jpg', '.jpeg', '.png')):
                skipped += 1
                continue
            image_path = folder / image_name
            if not image_path.is_file():
                skipped += 1
                continue
            video_name = Path(image_name).with_suffix('.mp4').name
            video_path = folder / video_name
            # 静止图复制为短序列，满足播放器与「有视频线索」展示。
            frames = [image_path] * 16
            if video_path.is_file() and video_path.stat().st_size > 64:
                ok = True
            else:
                ok = write_evidence_clip(frames, video_path, fps=8.0)
            if not ok:
                # 回退：单帧再试 OpenCV 直写
                frame = cv2.imread(str(image_path))
                if frame is None:
                    failed += 1
                    continue
                h, w = frame.shape[:2]
                tmp = video_path.with_suffix('.tmp.mp4')
                writer = cv2.VideoWriter(
                    str(tmp), cv2.VideoWriter_fourcc(*'mp4v'), 8.0, (w, h))
                if not writer.isOpened():
                    failed += 1
                    continue
                for _ in range(16):
                    writer.write(frame)
                writer.release()
                if not tmp.is_file() or tmp.stat().st_size < 64:
                    tmp.unlink(missing_ok=True)
                    failed += 1
                    continue
                tmp.replace(video_path)
            record['video'] = '/evidence/' + video_name
            db.execute(
                'UPDATE alerts SET record=? WHERE id=?',
                (json.dumps(record, ensure_ascii=False), alert_id))
            created += 1
    return {
        'ok': True,
        'scanned': scanned,
        'created': created,
        'skipped': skipped,
        'failed': failed,
    }
