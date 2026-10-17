"""批次 6：可再生运行文件清理（预览/执行、引用保护、安全校验、幂等）。"""
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
import time
import unittest
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from maintenance import (LOW_SPACE_BLOCK, LOW_SPACE_WARN, Maintenance)

DAY = 86400
UUID_A = 'a' * 32
UUID_B = 'b' * 32


def iso_ago(seconds):
    return datetime.fromtimestamp(time.time() - seconds, timezone.utc).isoformat()


def old(path, seconds):
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def write_run(root, camera='A', uuid=UUID_A, run_id='r1', latest=None, at_seconds=0):
    run_dir = root/'runtime'/camera/uuid/run_id
    camera_dir = run_dir/camera
    (camera_dir/'frames').mkdir(parents=True, exist_ok=True)
    status = {
        'cameraId': camera, 'state': 'running', 'results': len(latest or {}),
        'at': iso_ago(at_seconds), 'heartbeatSeq': 1,
        'latest': latest or {}, 'modelErrors': {}, 'alerts': {}, 'resource': {},
        'live': ''}
    (camera_dir/'status.json').write_text(json.dumps(status), encoding='utf-8')
    for record in (latest or {}).values():
        frame = run_dir/record['snapshot']
        frame.parent.mkdir(parents=True, exist_ok=True)
        frame.write_bytes(b'frame')
    return run_dir, camera_dir


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.work = Path(self.tmp.name)
        for name in ('covers', 'runtime', 'outbox', 'evidence'):
            (self.work/name).mkdir(exist_ok=True)
        self.runs = self.work/'runs'
        self.runs.mkdir()
        self.maintenance = Maintenance(self.work, self.runs)

    def tearDown(self):
        self.tmp.cleanup()

    # ---------- 帧 ----------

    def test_frames_protect_latest_and_recent(self):
        run_dir, camera_dir = write_run(self.work, latest={
            'person': {'snapshot': 'A/frames/3.jpg'}})
        frames = run_dir/'A'/'frames'
        latest_frame = frames/'3.jpg'
        old_frame = frames/'1.jpg'
        new_frame = frames/'2.jpg'
        latest_frame.write_bytes(b'latest')
        old_frame.write_bytes(b'old')
        new_frame.write_bytes(b'new')
        old(latest_frame, 600)
        old(old_frame, 600)
        old(new_frame, 10)
        # 活动运行目录本身受保护，不影响普通帧清理候选
        report = self.maintenance.plan()
        paths = {item['path'] for item in report['candidates']}
        self.assertIn(str(old_frame), paths)
        self.assertNotIn(str(latest_frame), paths)
        self.assertNotIn(str(new_frame), paths)

        executed = self.maintenance.execute()
        self.assertTrue(any(item['path'] == str(old_frame) for item in executed['deleted']))
        self.assertTrue(latest_frame.exists())
        self.assertTrue(new_frame.exists())
        self.assertFalse(old_frame.exists())
        # 幂等：再次执行不再删除
        again = self.maintenance.execute()
        self.assertEqual(again['deleted'], [])

    # ---------- 封面 ----------

    def test_cover_references_and_age(self):
        (self.work/'cameras.json').write_text(json.dumps([
            {'id': 'A', 'cover': '/covers/' + 'c' * 32 + '.jpg'}]), encoding='utf-8')
        referenced = self.work/'covers'/(('c' * 32) + '.jpg')
        stale = self.work/'covers'/(('d' * 32) + '.jpg')
        fresh = self.work/'covers'/(('e' * 32) + '.jpg')
        for file in (referenced, stale, fresh):
            file.write_bytes(b'cover')
        old(referenced, 2 * DAY)
        old(stale, 2 * DAY)
        old(fresh, 60)
        report = self.maintenance.plan()
        paths = {item['path'] for item in report['candidates']}
        self.assertIn(str(stale), paths)
        self.assertNotIn(str(referenced), paths)
        self.assertNotIn(str(fresh), paths)
        self.maintenance.execute()
        self.assertTrue(referenced.exists())
        self.assertFalse(stale.exists())

    # ---------- 运行目录 ----------

    def test_run_directory_retention_and_activity(self):
        old_run, _ = write_run(self.work, uuid=UUID_B, run_id='r2', at_seconds=8 * DAY)
        old(old_run.parent, 8 * DAY)
        active_run, _ = write_run(self.work, uuid=UUID_A, run_id='r1', at_seconds=0)
        # 未知数据库目录不视为可再生
        db_run, _ = write_run(self.work, uuid='0' * 32, run_id='r3', at_seconds=8 * DAY)
        (db_run/'unknown.db').write_bytes(b'db')
        old(db_run.parent, 8 * DAY)
        # 命名不合规目录只报告
        bad = self.work/'runtime'/'A'/'not-a-run'
        bad.mkdir(parents=True)
        old(bad, 8 * DAY)

        report = self.maintenance.plan()
        paths = {item['path'] for item in report['candidates']}
        self.assertIn(str(old_run.parent), paths)
        self.assertNotIn(str(active_run.parent), paths)
        self.assertNotIn(str(db_run.parent), paths)
        unknown_paths = {item['path'] for item in report['unknown']}
        self.assertIn(str(db_run.parent), unknown_paths)
        self.assertIn(str(bad), unknown_paths)

        self.maintenance.execute()
        self.assertFalse(old_run.parent.exists())
        self.assertTrue(active_run.parent.exists())
        self.assertTrue(db_run.parent.exists())

    # ---------- 冒烟目录 ----------

    def test_smoke_directory_retention(self):
        smoke = self.runs/('smoke-' + 'f' * 32)
        smoke.mkdir()
        (smoke/'analysis').mkdir()
        old(smoke, 8 * DAY)
        pending = self.runs/('smoke-' + '1' * 32)
        pending.mkdir()
        with closing(sqlite3.connect(pending/'outbox.db')) as db, db:
            db.execute("CREATE TABLE outbox (id TEXT, state TEXT, snapshot TEXT)")
            db.execute("INSERT INTO outbox VALUES ('x', 'pending', '')")
        old(pending, 8 * DAY)

        report = self.maintenance.plan()
        paths = {item['path'] for item in report['candidates']}
        self.assertIn(str(smoke), paths)
        self.assertNotIn(str(pending), paths)
        self.maintenance.execute()
        self.assertFalse(smoke.exists())
        self.assertTrue(pending.exists())

    # ---------- outbox 截图 ----------

    def test_outbox_media_reference_and_enqueue_race(self):
        camera_dir = self.work/'outbox'/'A'
        media = camera_dir/'media'
        media.mkdir(parents=True)
        db = camera_dir/'outbox.db'
        referenced = media/'ref.jpg'
        free_file = media/'free.jpg'
        fresh = media/'new.jpg'
        for file in (referenced, free_file, fresh):
            file.write_bytes(b'media')
        old(referenced, 2 * DAY)
        old(free_file, 2 * DAY)
        old(fresh, 60)
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('CREATE TABLE outbox (id TEXT, state TEXT, snapshot TEXT)')
            conn.execute('INSERT INTO outbox VALUES (?,?,?)', ('x', 'pending', str(referenced)))
        report = self.maintenance.plan()
        paths = {item['path'] for item in report['candidates']}
        self.assertIn(str(free_file), paths)
        self.assertNotIn(str(referenced), paths)
        self.assertNotIn(str(fresh), paths)
        # 清理期间入队：执行前新增对 free.jpg 的引用，执行必须重新检查并保留。
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute('INSERT INTO outbox VALUES (?,?,?)', ('y', 'dead', str(free_file)))
        result = self.maintenance.execute()
        self.assertTrue(free_file.exists(), '清理期间新增引用后不得删除文件')
        statuses = {item['path']: item for item in result['deleted']}
        self.assertNotIn(str(free_file), statuses)

    def test_outbox_reference_error_stops_batch(self):
        camera_dir = self.work/'outbox'/'A'
        media = camera_dir/'media'
        media.mkdir(parents=True)
        file = media/'x.jpg'
        file.write_bytes(b'x')
        old(file, 2 * DAY)
        (camera_dir/'outbox.db').write_bytes(b'not-a-db')
        with patch.object(Maintenance, '_outbox_snapshot_refs', side_effect=sqlite3.Error('locked')):
            report = self.maintenance.execute()
        self.assertTrue(report['errors'])
        self.assertIn('stopped', report)
        self.assertTrue(file.exists(), '引用查询失败时不得删除任何候选')

    # ---------- 安全性 ----------

    def test_directory_symlink_or_junction_is_reported_not_followed(self):
        outside = self.work/'outside'
        outside.mkdir()
        (outside/'keep.txt').write_text('keep', encoding='utf-8')
        link = self.work/'runtime'/'A'/UUID_B
        link.parent.mkdir(parents=True, exist_ok=True)
        created = subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(link), str(outside)],
            capture_output=True, text=True)
        if created.returncode != 0:
            self.skipTest('当前环境无法创建 junction')
        try:
            old(link, 8 * DAY)
            report = self.maintenance.plan()
            unknown = {item['path'] for item in report['unknown']}
            self.assertIn(str(link), unknown)
            self.assertNotIn(str(link), {item['path'] for item in report['candidates']})
            self.maintenance.execute()
            self.assertTrue((outside/'keep.txt').exists(), '不得删除 junction 指向的外部文件')
        finally:
            try:
                link.rmdir()
            except OSError:
                pass

    def test_outbox_media_under_junction_is_not_deleted(self):
        outside = self.work/'outside-media'
        outside.mkdir()
        victim = outside/'victim.jpg'
        victim.write_bytes(b'victim')
        old(victim, 2 * DAY)
        camera_dir = self.work/'outbox'/'A'
        camera_dir.mkdir(parents=True)
        with closing(sqlite3.connect(camera_dir/'outbox.db')) as db, db:
            db.execute('CREATE TABLE outbox (id TEXT, state TEXT, snapshot TEXT)')
        media = camera_dir/'media'
        created = subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(media), str(outside)],
            capture_output=True, text=True)
        if created.returncode != 0:
            self.skipTest('当前环境无法创建 junction')
        try:
            report = self.maintenance.plan()
            self.assertIn('victim.jpg',
                          {Path(item['path']).name for item in report['candidates']})
            result = self.maintenance.execute()
            self.assertTrue(victim.exists(), 'outbox 截图目录是 junction 时不得删除外部文件')
            skipped = (result.get('outbox') or {}).get('skipped') or []
            self.assertTrue(skipped, '应记录跳过原因而不是删除')
            self.assertTrue(any('拒绝删除' in item.get('reason', '') for item in skipped))
        finally:
            try:
                media.rmdir()
            except OSError:
                pass

    def test_delete_rejects_path_outside_root(self):
        outside = self.work/'outside.txt'
        outside.write_text('x', encoding='utf-8')
        result = self.maintenance._delete({'path': str(outside), 'kind': 'frame', 'reason': 'test'})
        self.assertIn('不在允许根目录内', result['reason'])
        self.assertTrue(outside.exists())

    # ---------- 低空间 ----------

    def test_disk_status_levels(self):
        with patch('maintenance.shutil.disk_usage',
                   return_value=SimpleNamespace(free=LOW_SPACE_BLOCK - 1, total=100 * 1024 ** 3)):
            self.assertEqual(self.maintenance.disk_status()['level'], 'blocked')
        with patch('maintenance.shutil.disk_usage',
                   return_value=SimpleNamespace(free=LOW_SPACE_WARN - 1, total=100 * 1024 ** 3)):
            self.assertEqual(self.maintenance.disk_status()['level'], 'warn')
        with patch('maintenance.shutil.disk_usage',
                   return_value=SimpleNamespace(free=LOW_SPACE_WARN + 1, total=100 * 1024 ** 3)):
            self.assertEqual(self.maintenance.disk_status()['level'], 'ok')

    # ---------- 预览与占用 ----------

    def test_preview_does_not_modify_files(self):
        (self.work/'cameras.json').write_text('[]', encoding='utf-8')
        cover = self.work/'covers'/(('a' * 32) + '.jpg')
        cover.write_bytes(b'x')
        old(cover, 2 * DAY)
        report = self.maintenance.plan()
        self.assertTrue(report['preview'])
        self.assertTrue(cover.exists(), '预览模式不得改动文件')
        self.assertTrue(any(item['path'] == str(cover) for item in report['candidates']))

    def test_busy_file_is_skipped(self):
        run_dir, _ = write_run(self.work, latest={})
        frames = run_dir/'A'/'frames'
        file = frames/'9.jpg'
        file.write_bytes(b'x')
        old(file, 600)
        handle = open(file, 'rb')
        try:
            result = self.maintenance.execute()
            self.assertTrue(any(item['path'] == str(file) for item in result['skipped']))
            self.assertTrue(file.exists())
        finally:
            handle.close()
        self.maintenance.execute()
        self.assertFalse(file.exists())


if __name__ == '__main__':
    unittest.main()
