"""历史未处置积压批量清理。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from alert_bulk import bulk_dispose_pending
from alert_disposition import disposition_of


class BulkDisposeTests(unittest.TestCase):
    def test_keeps_recent_and_clears_old(self):
        now = datetime(2026, 11, 9, 8, 0, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'events.db'
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.execute(
                    'CREATE TABLE alerts (id TEXT PRIMARY KEY, camera TEXT, at TEXT, record TEXT)')
                old_at = (now - timedelta(hours=48)).isoformat()
                new_at = (now - timedelta(hours=2)).isoformat()
                for aid, at in [('old', old_at), ('new', new_at)]:
                    record = {
                        'id': aid, 'eventId': 'no_helmet', 'eventName': '未戴安全帽',
                        'capturedAt': at, 'image': f'/evidence/{aid}.jpg',
                    }
                    conn.execute(
                        'INSERT INTO alerts VALUES (?,?,?,?)',
                        (aid, 'cam', at, json.dumps(record)))
            result = bulk_dispose_pending(
                db, keep_within_hours=24, now=now, handled_by='tester')
            self.assertEqual(result['updated'], 1)
            self.assertEqual(result['keptRecent'], 1)
            with closing(sqlite3.connect(db)) as conn:
                rows = {
                    aid: json.loads(raw)
                    for aid, raw in conn.execute('SELECT id, record FROM alerts')
                }
            self.assertEqual(disposition_of(rows['old']), 'handled')
            self.assertEqual(rows['old']['handledBy'], 'tester')
            self.assertEqual(disposition_of(rows['new']), 'pending')


if __name__ == '__main__':
    unittest.main()
