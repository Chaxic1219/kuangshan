"""控制台事件导出接口的端到端检查。"""
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from console import Controller, make_handler


def seed(controller, count=2):
    with closing(sqlite3.connect(controller.database)) as db, db:
        for i in range(count):
            record = {
                'id': f'e{i}', 'alertCode': f'ALT-AI-{i:032d}', 'eventId': 'intrusion',
                'eventName': '人员区域入侵', 'cameraName': '综采工作面', 'engine': 'yolo',
                'capturedAt': f'2026-10-14T10:0{i}:00+00:00', 'image': f'/evidence/e{i}.jpg',
                'detections': [{'label': 'person', 'confidence': 0.81 + i / 100,
                                'regionName': '禁区一'}],
            }
            db.execute('INSERT INTO alerts VALUES (?,?,?,?)',
                       (f'e{i}', 'cam1', record['capturedAt'], json.dumps(record, ensure_ascii=False)))


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.c = Controller(Path(self.tmp.name))
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.c, 'tok'))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.c.close()
        self.tmp.cleanup()

    def _get(self, path):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        conn.request('GET', path, headers={'Host': f'127.0.0.1:{self.server.server_port}'})
        response = conn.getresponse()
        body = response.read()
        headers = dict(response.getheaders())
        conn.close()
        return response.status, headers, body

    def test_csv_export_shape(self):
        seed(self.c, 2)
        text = self.c.alerts_csv()
        lines = [l for l in text.splitlines() if l.strip()]
        self.assertEqual(len(lines), 3, '表头 + 2 条记录')
        self.assertIn('报警编号', lines[0])
        self.assertIn('ALT-AI-', lines[1])
        self.assertIn('人员区域入侵', lines[1])
        self.assertIn('禁区一', lines[1])

    def test_csv_export_http_headers(self):
        seed(self.c, 1)
        status, headers, body = self._get('/api/alerts/export.csv')
        self.assertEqual(status, 200)
        self.assertIn('text/csv', headers['Content-Type'])
        self.assertIn('attachment', headers['Content-Disposition'])
        self.assertTrue(body.startswith(b'\xef\xbb\xbf'), '应带 UTF-8 BOM 以便 Excel 正确显示中文')
        self.assertIn('人员区域入侵'.encode('utf-8'), body)

    def test_export_requires_local_host(self):
        """导出同样受 Host 白名单保护。"""
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        conn.request('GET', '/api/alerts/export.csv', headers={'Host': 'evil.example'})
        response = conn.getresponse()
        response.read()
        self.assertEqual(response.status, 403)
        conn.close()

    def test_state_reports_bridge_detail(self):
        state = self.c.state()
        self.assertIn('alertBridge', state)
        self.assertIn('detail', state['alertBridge'])
        for key in ('reported', 'failed', 'pending'):
            self.assertIn(key, state['alertBridge']['detail'])

    def test_empty_export_still_valid(self):
        status, headers, body = self._get('/api/alerts/export.csv')
        self.assertEqual(status, 200)
        self.assertIn('报警编号', body.decode('utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
