"""告警大屏聚合：范围、边界、坏 JSON、多目标一条事件、删除摄像头。"""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

import alert_dashboard as dash

TZ = timezone(timedelta(hours=8))


def seed(db_path, rows):
    with closing(sqlite3.connect(db_path)) as db, db:
        db.execute('CREATE TABLE alerts (id TEXT PRIMARY KEY, camera TEXT, at TEXT, record TEXT)')
        for item in rows:
            db.execute('INSERT INTO alerts VALUES (?,?,?,?)', item)


def alert_row(aid, camera, at, event_id='no_helmet', name='未戴安全帽', camera_name=None, detections=None, bad=False):
    if bad:
        return (aid, camera, at, '{bad')
    record = {
        'id': aid, 'cameraId': camera, 'cameraName': camera_name or camera,
        'eventId': event_id, 'eventName': name,
        'capturedAt': at, 'image': f'/evidence/{aid}.jpg',
        'detections': detections if detections is not None else [
            {'label': 'NO-Hardhat', 'confidence': .9, 'bbox': [0, 0, .2, .2]},
            {'label': 'NO-Hardhat', 'confidence': .8, 'bbox': [.3, .3, .5, .5]},
        ],
        'alarm': True,
    }
    return (aid, camera, at, json.dumps(record, ensure_ascii=False))


class AlertDashboardTests(unittest.TestCase):
    def test_today_window_and_totals_match(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'events.db'
            now = datetime(2026, 10, 25, 10, 0, tzinfo=timezone.utc)
            local_today = now.astimezone(TZ).replace(hour=1, minute=0, second=0, microsecond=0)
            inside = local_today.astimezone(timezone.utc).isoformat()
            yesterday = (local_today - timedelta(days=1)).astimezone(timezone.utc).isoformat()
            seed(db, [
                alert_row('a1', 'cam1', inside, 'no_helmet'),
                alert_row('a2', 'cam1', inside, 'fire', '火焰检测', detections=[{'label': 'fire', 'confidence': .9}]),
                alert_row('a3', 'cam2', inside, 'no_helmet'),
                alert_row('a4', 'cam2', yesterday, 'no_helmet'),  # 昨日，不应计入今日
                alert_row('bad', 'cam1', inside, bad=True),
            ])
            result = dash.dashboard(db, preset='today', now=now)
            self.assertEqual(result['summary']['totalEvents'], 3)
            self.assertEqual(result['summary']['cameraCount'], 2)
            self.assertEqual(result['summary']['typeCount'], 2)
            self.assertEqual(result['excludedRecords'], 1)
            self.assertEqual(sum(item['count'] for item in result['trend']), 3)
            self.assertEqual(sum(item['count'] for item in result['byType']), 3)
            self.assertEqual(sum(item['count'] for item in result['byCamera'] if not item.get('remainder')), 3)
            # 一条记录两个检测目标仍计 1 次事件
            dual = next(item for item in result['recent'] if item['id'] == 'a1')
            self.assertEqual(dual['detectionCount'], 2)
            helmet = next(item for item in result['byType'] if item['eventId'] == 'no_helmet')
            self.assertEqual(helmet['count'], 2)

    def test_half_open_boundary(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'events.db'
            begin = datetime(2026, 10, 25, 0, 0, tzinfo=TZ).astimezone(timezone.utc)
            end = begin + timedelta(days=1)
            seed(db, [
                alert_row('in', 'c1', begin.isoformat()),
                alert_row('out', 'c1', end.isoformat()),
            ])
            result = dash.dashboard(
                db, preset='custom', start=begin.isoformat(), end=end.isoformat(), now=end)
            self.assertEqual(result['summary']['totalEvents'], 1)
            self.assertEqual(result['recent'][0]['id'], 'in')

    def test_deleted_camera_still_listed(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'events.db'
            now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            at = (now - timedelta(hours=1)).isoformat()
            seed(db, [alert_row('a1', 'gone', at, camera_name='已删摄像头')])
            result = dash.dashboard(db, preset='last24h', now=now, cameras=[
                {'id': 'live1', 'name': '在线', 'running': True, 'state': 'running', 'tasks': []},
            ])
            ids = {item['cameraId']: item for item in result['filterCameras']}
            self.assertEqual(ids['gone']['status'], 'deleted')
            self.assertEqual(ids['gone']['cameraName'], '已删摄像头')
            self.assertEqual(ids['live1']['status'], 'active')

    def test_detect_only_hint(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'events.db'
            seed(db, [])
            now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            result = dash.dashboard(db, preset='today', now=now, cameras=[{
                'id': 'c1', 'name': 'USB', 'running': True, 'state': 'running',
                'tasks': [{'eventId': 'person', 'enabled': True}],
            }])
            self.assertEqual(result['summary']['totalEvents'], 0)
            self.assertTrue(any('检测场景' in tip for tip in result['hints']))

    def test_query_pagination(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'events.db'
            now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            rows = []
            for i in range(5):
                # 半开区间 [from,to)：发生时间须严格早于查询时刻 to
                at = (now - timedelta(minutes=i + 1)).isoformat()
                rows.append(alert_row(f'id{i}', 'c1', at))
            seed(db, rows)
            page1 = dash.query_alerts(db, preset='last24h', page=1, page_size=2, now=now)
            page2 = dash.query_alerts(db, preset='last24h', page=2, page_size=2, now=now)
            self.assertEqual(page1['total'], 5)
            self.assertEqual(len(page1['rows']), 2)
            self.assertEqual(page1['rows'][0]['id'], 'id0')
            self.assertEqual(page2['rows'][0]['id'], 'id2')

    def test_query_location_filter(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'events.db'
            now = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
            at = (now - timedelta(minutes=5)).isoformat()
            seed(db, [
                alert_row('east', 'c-east', at),
                alert_row('west', 'c-west', at),
            ])
            cameras = [
                {'id': 'c-east', 'name': '东口', 'location': '东区'},
                {'id': 'c-west', 'name': '西口', 'location': '西区'},
            ]
            result = dash.query_alerts(
                db, preset='last24h', location='东区', cameras=cameras, now=now)
            self.assertEqual(result['total'], 1)
            self.assertEqual(result['rows'][0]['id'], 'east')
            self.assertEqual(result['rows'][0]['location'], '东区')
            self.assertEqual(result['filters']['location'], '东区')

    def test_max_range(self):
        begin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = begin + timedelta(days=91)
        with self.assertRaises(dash.DashboardError):
            dash.resolve_window('custom', begin.isoformat(), end.isoformat())

    def test_high_risk_count_in_summary(self):
        """E2：总览需展示周期内高风险告警数。"""
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'events.db'
            now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
            local = now.astimezone(TZ).replace(hour=10, minute=0, second=0, microsecond=0)
            inside = local.astimezone(timezone.utc).isoformat()
            seed(db, [
                alert_row('h1', 'c1', inside, 'no_helmet'),  # high
                alert_row('h2', 'c1', inside, 'fire', '火焰检测',
                          detections=[{'label': 'fire', 'confidence': .9}]),
                alert_row('m1', 'c1', inside, 'brightness', '亮度异常',
                          detections=[{'label': 'bright', 'confidence': .9}]),
            ])
            result = dash.dashboard(db, preset='today', now=now)
            self.assertEqual(result['summary']['totalEvents'], 3)
            self.assertEqual(result['summary']['highRiskCount'], 2)

    def test_disposition_kpi_in_summary(self):
        """E4：处置率 = 已离开 pending / 总数；平均时长仅对已处置样本。"""
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'events.db'
            now = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
            local = now.astimezone(TZ).replace(hour=10, minute=0, second=0, microsecond=0)
            t0 = local.astimezone(timezone.utc)
            t1 = (t0 + timedelta(minutes=10)).isoformat()
            t2 = (t0 + timedelta(minutes=40)).isoformat()
            created = t0.isoformat()
            rows = [
                alert_row('p1', 'c1', created, 'no_helmet'),
                alert_row('h1', 'c1', created, 'fire', '火焰检测',
                          detections=[{'label': 'fire', 'confidence': .9}]),
                alert_row('f1', 'c1', created, 'brightness', '亮度异常',
                          detections=[{'label': 'bright', 'confidence': .9}]),
            ]
            # 注入处置字段
            parsed = []
            for aid, cam, at, raw in rows:
                rec = json.loads(raw)
                if aid == 'h1':
                    rec['disposition'] = 'handled'
                    rec['handledAt'] = t1
                    rec['handledBy'] = 'admin'
                elif aid == 'f1':
                    rec['disposition'] = 'false_positive'
                    rec['handledAt'] = t2
                    rec['handledBy'] = 'op'
                parsed.append((aid, cam, at, json.dumps(rec, ensure_ascii=False)))
            seed(db, parsed)
            result = dash.dashboard(db, preset='today', now=now)
            summary = result['summary']
            self.assertEqual(summary['totalEvents'], 3)
            self.assertEqual(summary['disposedCount'], 2)
            self.assertEqual(summary['pendingCount'], 1)
            self.assertEqual(summary['dispositionRate'], round(2 / 3, 4))
            # (10min + 40min) / 2 = 25min = 1500s
            self.assertEqual(summary['avgDispositionSeconds'], 1500.0)


if __name__ == '__main__':
    unittest.main()
