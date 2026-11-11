"""历史告警视频线索回填。"""
from __future__ import annotations

from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from evidence_backfill import backfill_missing_videos


class EvidenceBackfillTests(unittest.TestCase):
    def test_creates_video_and_updates_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / 'evidence'
            evidence.mkdir()
            jpg = evidence / 'a1.jpg'
            cv2.imwrite(str(jpg), np.full((48, 64, 3), 90, np.uint8))
            db = root / 'events.db'
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute(
                    'CREATE TABLE alerts (id TEXT PRIMARY KEY, camera TEXT, at TEXT, record TEXT)')
                record = {
                    'id': 'a1', 'eventId': 'person', 'image': '/evidence/a1.jpg',
                }
                conn.execute(
                    'INSERT INTO alerts VALUES (?,?,?,?)',
                    ('a1', 'c', '2026-11-01T00:00:00+00:00', json.dumps(record)))
            result = backfill_missing_videos(db, evidence)
            self.assertEqual(result['created'], 1)
            self.assertTrue((evidence / 'a1.mp4').is_file())
            with closing(sqlite3.connect(db)) as conn:
                raw = conn.execute('SELECT record FROM alerts WHERE id=?', ('a1',)).fetchone()[0]
            self.assertEqual(json.loads(raw)['video'], '/evidence/a1.mp4')


if __name__ == '__main__':
    unittest.main()
