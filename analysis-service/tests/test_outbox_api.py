"""批次 5：控制台待发送队列接口（查询、分页、人工重试、访问校验）。"""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path

from console import Controller, make_handler

TOKEN = 'outbox-token'


def seed_outbox(work, camera_id, dead=3, pending=1):
    path = work/'outbox'/camera_id/'outbox.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('''CREATE TABLE IF NOT EXISTS outbox (
            id TEXT PRIMARY KEY, alert_code TEXT, payload TEXT, snapshot TEXT,
            attempts INTEGER DEFAULT 0, next_at REAL DEFAULT 0,
            state TEXT DEFAULT 'pending', last_error TEXT DEFAULT '', updated_at REAL DEFAULT 0)''')
        for index in range(dead):
            payload = json.dumps({
                'record': {'eventId': 'no_helmet', 'eventName': '未戴安全帽'},
                'detection': {'label': 'NO-Hardhat', 'confidence': .9},
                'index': index, 'alertCode': 'ALT-AI-%d' % index, 'snapshot': ''})
            db.execute(
                'INSERT INTO outbox (id, alert_code, payload, snapshot, attempts, next_at, '
                'state, last_error, updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                ('dead-%d' % index, 'ALT-AI-%d' % index, payload, '', 2, 0, 'dead',
                 '告警接口 HTTP 400', time.time() - index))
        for index in range(pending):
            db.execute(
                'INSERT INTO outbox (id, alert_code, payload, snapshot, attempts, next_at, '
                'state, last_error, updated_at) VALUES (?,?,?,?,0,0,?,?,?)',
                ('pending-%d' % index, 'ALT-AI-P%d' % index, '{}', '', 'pending', '', time.time()))


class OutboxApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.controller = Controller(self.work)
        self.camera = self.controller.store.save({
            'id': 'cam1', 'name': '测试视频', 'url': 'rtsp://localhost/a',
            'tasks': [], 'regions': [], 'cover': '/covers/x.jpg'})
        seed_outbox(self.work, 'cam1', dead=3, pending=1)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.controller, TOKEN))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.controller.close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=TOKEN, host=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        headers = {'Host': host or '127.0.0.1:%d' % self.server.server_port}
        if body is not None:
            payload = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
            headers['Content-Length'] = str(len(payload))
        if token:
            headers['X-Console-Token'] = token
        conn.request(method, path, body=payload if body is not None else None, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode('utf-8'))
        conn.close()
        return response.status, data

    def outbox_state(self, outbox_id):
        path = self.work/'outbox'/'cam1'/'outbox.db'
        with closing(sqlite3.connect(path)) as db:
            return db.execute('SELECT state FROM outbox WHERE id=?', (outbox_id,)).fetchone()[0]

    def test_list_dead_rows(self):
        status, data = self.request('GET', '/api/alerts/outbox?cameraId=cam1&state=dead')
        self.assertEqual(status, 200)
        self.assertEqual(len(data['rows']), 3)
        row = data['rows'][0]
        for key in ('cameraId', 'id', 'eventName', 'state', 'attempts', 'error', 'updatedAt'):
            self.assertIn(key, row)
        self.assertEqual(row['eventName'], '未戴安全帽')
        self.assertIn('HTTP 400', row['error'])
        self.assertIn('T', row['updatedAt'])

    def test_limit_is_capped_at_100(self):
        status, data = self.request('GET', '/api/alerts/outbox?cameraId=cam1&state=dead&limit=1000')
        self.assertEqual(status, 200)
        self.assertEqual(data['limit'], 100)
        self.assertLessEqual(len(data['rows']), 100)

    def test_pagination(self):
        status, data = self.request('GET', '/api/alerts/outbox?cameraId=cam1&state=dead&limit=1&offset=1')
        self.assertEqual(status, 200)
        self.assertEqual(len(data['rows']), 1)
        self.assertEqual(data['offset'], 1)

    def test_invalid_parameters(self):
        for path in ('/api/alerts/outbox?cameraId=cam1&state=bad',
                     '/api/alerts/outbox?cameraId=cam1&limit=x',
                     '/api/alerts/outbox?cameraId=../evil'):
            status, _ = self.request('GET', path)
            self.assertEqual(status, 400, path)

    def test_foreign_host_rejected(self):
        status, _ = self.request('GET', '/api/alerts/outbox?state=dead', host='evil.example')
        self.assertEqual(status, 403)

    def test_retry_moves_dead_to_pending_with_prompt(self):
        status, data = self.request('POST', '/api/alerts/retry', {'cameraId': 'cam1', 'id': 'dead-0'})
        self.assertEqual(status, 200)
        self.assertTrue(data['ok'])
        self.assertEqual(data['retried'], 1)
        self.assertFalse(data['running'])
        self.assertIn('下次启动分析后发送', data['message'])
        self.assertEqual(self.outbox_state('dead-0'), 'pending')

    def test_retry_is_idempotent_and_validates(self):
        self.request('POST', '/api/alerts/retry', {'cameraId': 'cam1', 'id': 'dead-0'})
        status, data = self.request('POST', '/api/alerts/retry', {'cameraId': 'cam1', 'id': 'dead-0'})
        self.assertEqual(status, 200)
        self.assertFalse(data['ok'])
        status, _ = self.request('POST', '/api/alerts/retry', {'cameraId': 'missing', 'id': 'x'})
        self.assertEqual(status, 400)
        status, data = self.request('POST', '/api/alerts/retry', {'cameraId': 'cam1', 'id': '../x'})
        self.assertEqual(status, 400)

    def test_retry_requires_token(self):
        status, _ = self.request('POST', '/api/alerts/retry',
                                 {'cameraId': 'cam1', 'id': 'dead-0'}, token='')
        self.assertEqual(status, 403)


if __name__ == '__main__':
    unittest.main()
