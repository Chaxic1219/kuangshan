"""告警处置状态机与查询筛选。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import alert_disposition as disposition
from alert_dashboard import ensure_indexes, query_alerts


class NormalizeTests(unittest.TestCase):
    def test_missing_field_is_pending(self):
        self.assertEqual(disposition.disposition_of({}), 'pending')
        self.assertEqual(disposition.disposition_of(None), 'pending')

    def test_risk_levels(self):
        self.assertEqual(disposition.risk_level('fire'), 'high')
        self.assertEqual(disposition.risk_level('brightness'), 'medium')
        self.assertEqual(disposition.risk_level('person'), 'low')


class ApplyTests(unittest.TestCase):
    def test_handle_and_false_positive(self):
        base = {'id': 'a1', 'eventId': 'fire', 'eventName': '火焰'}
        handled = disposition.apply_disposition(base, 'handled', handled_by='值班员', note='已纠正')
        self.assertEqual(handled['disposition'], 'handled')
        self.assertEqual(handled['handledBy'], '值班员')
        self.assertEqual(handled['dispositionNote'], '已纠正')
        self.assertTrue(handled['handledAt'])
        self.assertEqual(base.get('disposition'), None)

        again = disposition.apply_disposition(
            {'id': 'a2', 'eventId': 'smoke'}, 'false_positive', handled_by='值班')
        self.assertEqual(again['disposition'], 'false_positive')

    def test_rejects_invalid_and_repeat(self):
        with self.assertRaises(ValueError):
            disposition.apply_disposition({'id': 'x'}, 'pending')
        done = disposition.apply_disposition({'id': 'y', 'eventId': 'fire'}, 'handled')
        with self.assertRaises(ValueError):
            disposition.apply_disposition(done, 'false_positive')


class QueryFilterTests(unittest.TestCase):
    def test_query_filters_by_disposition(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'events.db'
            ensure_indexes(db)
            now = datetime(2026, 10, 28, 10, 0, tzinfo=timezone.utc)
            rows = []
            for i, disp in enumerate(('pending', 'handled', 'false_positive')):
                at = (now - timedelta(hours=i)).isoformat()
                aid = f'id{i}'
                record = {
                    'id': aid, 'eventId': 'fire', 'eventName': '火焰',
                    'cameraId': 'c1', 'cameraName': 'cam', 'image': '',
                    'detections': [], 'alarm': True,
                }
                if disp != 'pending':
                    record['disposition'] = disp
                    record['handledAt'] = at
                rows.append((aid, 'c1', at, json.dumps(record, ensure_ascii=False)))
            # 旧数据无 disposition 字段
            old_at = (now - timedelta(hours=5)).isoformat()
            rows.append(('old1', 'c1', old_at, json.dumps({
                'id': 'old1', 'eventId': 'smoke', 'eventName': '烟雾',
                'cameraId': 'c1', 'cameraName': 'cam', 'image': '',
                'detections': [], 'alarm': True,
            }, ensure_ascii=False)))
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.executemany('INSERT INTO alerts VALUES (?,?,?,?)', rows)

            with patch('alert_dashboard.datetime') as mocked:
                # resolve_window uses datetime.now — patch via now= parameter instead
                pass
            pending = query_alerts(
                db, preset='custom',
                start=(now - timedelta(days=1)).isoformat(),
                end=(now + timedelta(hours=1)).isoformat(),
                disposition='pending', page=1, page_size=50, now=now)
            self.assertEqual(pending['total'], 2)  # explicit pending + old
            self.assertTrue(all(r['disposition'] == 'pending' for r in pending['rows']))

            handled = query_alerts(
                db, preset='custom',
                start=(now - timedelta(days=1)).isoformat(),
                end=(now + timedelta(hours=1)).isoformat(),
                disposition='handled', now=now)
            self.assertEqual(handled['total'], 1)
            self.assertEqual(handled['rows'][0]['disposition'], 'handled')
            self.assertEqual(handled['rows'][0]['riskLevel'], 'high')

    def test_query_filters_by_risk_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'events.db'
            ensure_indexes(db)
            now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
            rows = [
                ('high1', 'c1', (now - timedelta(hours=1)).isoformat(), json.dumps({
                    'id': 'high1', 'eventId': 'fire', 'eventName': '火焰',
                    'cameraId': 'c1', 'cameraName': 'cam', 'image': '',
                    'detections': [], 'alarm': True,
                }, ensure_ascii=False)),
                ('med1', 'c1', (now - timedelta(hours=2)).isoformat(), json.dumps({
                    'id': 'med1', 'eventId': 'brightness', 'eventName': '亮度',
                    'cameraId': 'c1', 'cameraName': 'cam', 'image': '',
                    'detections': [], 'alarm': True,
                }, ensure_ascii=False)),
            ]
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.executemany('INSERT INTO alerts VALUES (?,?,?,?)', rows)
            high = query_alerts(
                db, preset='custom',
                start=(now - timedelta(days=1)).isoformat(),
                end=(now + timedelta(hours=1)).isoformat(),
                risk_level='high', now=now)
            self.assertEqual(high['total'], 1)
            self.assertEqual(high['rows'][0]['id'], 'high1')
            self.assertEqual(high['filters']['riskLevel'], 'high')

    def test_risk_sort_keeps_newest_first_within_level(self):
        """处置台：高风险优先，同风险取最新，避免旧种子堵住实时告警。"""
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / 'events.db'
            ensure_indexes(db)
            now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
            specs = [
                ('old_high', 'fire', now - timedelta(days=3)),
                ('new_high', 'no_helmet', now - timedelta(minutes=1)),
                ('new_medium', 'brightness', now - timedelta(minutes=2)),
            ]
            rows = []
            for aid, event_id, moment in specs:
                at = moment.isoformat()
                rows.append((aid, 'c1', at, json.dumps({
                    'id': aid, 'eventId': event_id, 'eventName': event_id,
                    'cameraId': 'c1', 'cameraName': 'cam', 'image': '',
                    'detections': [], 'alarm': True,
                }, ensure_ascii=False)))
            with closing(sqlite3.connect(db)) as conn, conn:
                conn.executemany('INSERT INTO alerts VALUES (?,?,?,?)', rows)

            result = query_alerts(
                db, preset='custom',
                start=(now - timedelta(days=7)).isoformat(),
                end=(now + timedelta(hours=1)).isoformat(),
                disposition='pending', sort='risk', now=now)
            ids = [row['id'] for row in result['rows']]
            self.assertEqual(ids, ['new_high', 'old_high', 'new_medium'])


if __name__ == '__main__':
    unittest.main()
