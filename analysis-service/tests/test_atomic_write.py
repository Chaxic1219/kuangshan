"""读者短暂持有文件时，原子写入不能导致分析进程退出。"""
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from catalog import atomic_json
from service import write_atomic
import console


class AtomicWriteTests(unittest.TestCase):
    def test_status_read_retries_transient_permission_error(self):
        with patch.object(Path, 'read_bytes', side_effect=[PermissionError(), b'{"state":"running"}']):
            self.assertEqual(console.read_json(Path('status.json'), {}), {'state': 'running'})

    def test_preview_read_retries_transient_permission_error(self):
        with patch.object(Path, 'read_bytes', side_effect=[PermissionError(), b'jpeg']):
            self.assertEqual(console.read_shared_bytes(Path('live.jpg')), b'jpeg')

    def test_preview_and_status_retry_transient_sharing_conflict(self):
        for writer, value, expected in [(write_atomic, b'new', b'new'),
                                        (atomic_json, {'state': 'running'}, None)]:
            with self.subTest(writer=writer.__name__), tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp)/'current.json'
                target.write_bytes(b'old')
                replace = Path.replace
                attempts = []

                def occupied(source, destination):
                    attempts.append(1)
                    if len(attempts) < 3:
                        self.assertEqual(target.read_bytes(), b'old')
                        raise PermissionError('文件被读者短暂占用')
                    return replace(source, destination)

                with patch.object(Path, 'replace', occupied):
                    writer(target, value)
                self.assertEqual(len(attempts), 3)
                if expected is not None:
                    self.assertEqual(target.read_bytes(), expected)
                else:
                    self.assertEqual(json.loads(target.read_text(encoding='utf-8')), value)

    def test_permanent_permission_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'live.jpg'
            target.write_bytes(b'old')
            with patch.object(Path, 'replace', side_effect=PermissionError('永久拒绝')) as replace:
                with self.assertRaises(PermissionError):
                    write_atomic(target, b'new')
            self.assertLessEqual(replace.call_count, 6)
            self.assertEqual(target.read_bytes(), b'old')

    def test_other_io_errors_are_not_retried(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(Path, 'replace', side_effect=OSError('磁盘错误')) as replace:
                with self.assertRaises(OSError):
                    write_atomic(Path(tmp)/'live.jpg', b'new')
            self.assertEqual(replace.call_count, 1)

    @unittest.skipUnless(sys.platform == 'win32', '验证 Windows 实际文件共享行为')
    def test_windows_reader_releases_file_during_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)/'live.jpg'
            target.write_bytes(b'old')
            reader = target.open('rb')
            timer = threading.Timer(.05, reader.close)
            timer.start()
            try:
                write_atomic(target, b'new')
                self.assertEqual(target.read_bytes(), b'new')
            finally:
                timer.join()
                reader.close()
