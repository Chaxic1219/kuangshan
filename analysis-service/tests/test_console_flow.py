"""控制台：演示视频 → 封面 → 保存 → 启停 的端到端检查。"""
import json
import tempfile
import threading
import time
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from console import Controller, make_handler


class ConsoleFlowTests(unittest.TestCase):
    def test_sample_probe_save_start_stop(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            controller = Controller(work)
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(controller, 'flow-token'))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            host, port = '127.0.0.1', server.server_port
            try:
                samples = json.loads(self._get(host, port, '/api/samples'))
                self.assertGreaterEqual(len(samples), 1)
                url = samples[0]['url']
                self.assertNotIn('\\', url)

                probe_body = json.dumps({'url': url}).encode()
                probe = self._post(host, port, '/api/cameras/probe', probe_body, 'flow-token')
                self.assertIn('coverToken', probe)
                self.assertTrue((work / 'covers' / (probe['coverToken'] + '.jpg')).is_file())

                payload = {
                    'id': '', 'name': '联调视频', 'location': '测试区', 'url': url,
                    'coverToken': probe['coverToken'],
                    'tasks': [
                        {'eventId': 'person', 'confidence': 0.3, 'interval': 0.5, 'cooldown': 5,
                         'enabled': True, 'regionIds': []},
                        {'eventId': 'brightness', 'confidence': 0.4, 'interval': 0.5, 'cooldown': 5,
                         'enabled': True, 'regionIds': []},
                    ],
                    'regions': [],
                }
                saved = self._post(host, port, '/api/cameras/save',
                                   json.dumps(payload).encode(), 'flow-token')
                cid = saved['camera']['id']
                self._post(host, port, '/api/cameras/start',
                           json.dumps({'id': cid}).encode(), 'flow-token')

                deadline = time.monotonic() + 45
                results = 0
                while time.monotonic() < deadline:
                    state = json.loads(self._get(host, port, '/api/state'))
                    cam = next(c for c in state['cameras'] if c['id'] == cid)
                    results = cam.get('results') or 0
                    if cam.get('running') and results > 0:
                        break
                    time.sleep(1)
                self.assertGreater(results, 0, '启动后应产生分析结果')

                self._post(host, port, '/api/cameras/stop',
                           json.dumps({'id': cid}).encode(), 'flow-token')
                state = json.loads(self._get(host, port, '/api/state'))
                cam = next(c for c in state['cameras'] if c['id'] == cid)
                self.assertFalse(cam['running'])
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
                controller.close()

    def _get(self, host, port, path):
        conn = HTTPConnection(host, port, timeout=30)
        conn.request('GET', path, headers={'Host': f'{host}:{port}'})
        response = conn.getresponse()
        body = response.read().decode()
        self.assertEqual(response.status, 200, body)
        conn.close()
        return body

    def _post(self, host, port, path, body, token):
        conn = HTTPConnection(host, port, timeout=30)
        conn.request(
            'POST', path, body=body,
            headers={'Host': f'{host}:{port}', 'Content-Type': 'application/json',
                     'Content-Length': str(len(body)), 'X-Console-Token': token})
        response = conn.getresponse()
        text = response.read().decode()
        self.assertEqual(response.status, 200, text)
        conn.close()
        return json.loads(text)


if __name__ == '__main__':
    unittest.main()
