"""本机摄像头接入接口：设备枚举、映射写入、移除与 live 流控制。"""
import json
import tempfile
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import camera_source
import console
from console import Controller, make_handler

TOKEN = 'live-token'


class LiveCameraApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        self.patches = [
            patch.object(camera_source, 'LIVE_CONFIG', self.work/'mediamtx-live.yml'),
            patch.object(console, 'STREAM_SESSION', self.work/'session.json'),
            patch.object(camera_source, 'list_camera_devices',
                         side_effect=lambda with_error=False: (
                             ([{'name': 'USB Camera A', 'usable': True},
                               {'name': 'Virtual B', 'usable': False}], '')
                             if with_error else
                             [{'name': 'USB Camera A', 'usable': True},
                              {'name': 'Virtual B', 'usable': False}])),
        ]
        for item in self.patches:
            item.start()
        self.controller = Controller(self.work)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.controller, TOKEN))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.controller.close()
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def request(self, method, path, body=None, token=TOKEN):
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        headers = {'Host': '127.0.0.1:%d' % self.server.server_port}
        payload = None
        if body is not None:
            payload = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
            headers['Content-Length'] = str(len(payload))
        if token:
            headers['X-Console-Token'] = token
        conn.request(method, path, body=payload, headers=headers)
        response = conn.getresponse()
        data = json.loads(response.read().decode('utf-8'))
        conn.close()
        return response.status, data

    def test_list_devices_and_live_paths(self):
        status, data = self.request('GET', '/api/cameras/devices')
        self.assertEqual(status, 200)
        self.assertEqual([item['name'] for item in data['devices']],
                         ['USB Camera A', 'Virtual B'])
        self.assertFalse(data['devices'][1]['usable'])
        self.assertEqual(data['livePaths'], [])
        self.assertFalse(data['stream']['running'])
        self.assertEqual(data['error'], '')

    def test_prepare_uses_normal_probe_without_saving_camera(self):
        with patch.object(self.controller, 'control_live_streams') as start, \
                patch.object(camera_source, 'wait_live_ready', return_value=True), \
                patch.object(self.controller, 'probe', return_value={
                    'coverToken': 'preview', 'cover': '/covers/preview.jpg'}) as probe:
            status, data = self.request('POST', '/api/cameras/live/prepare',
                                        {'deviceId': 'USB Camera A'})
        self.assertEqual(status, 200)
        self.assertEqual(data['url'], 'rtsp://127.0.0.1:8554/livecam1')
        self.assertEqual(data['coverToken'], 'preview')
        probe.assert_called_once_with(data['url'])
        start.assert_called_once_with('start')
        self.assertEqual(self.controller.store.cameras, [])
        status, saved = self.request('POST', '/api/cameras/save', {
            'name': '普通摄像头', 'url': data['url'], 'coverToken': 'preview',
            'tasks': [], 'regions': []})
        self.assertEqual(status, 200)
        self.assertEqual(saved['camera']['cover'], '/covers/preview.jpg')
        self.assertEqual(saved['camera']['liveDeviceId'], 'USB Camera A')
        self.assertEqual(saved['camera']['liveDeviceName'], 'USB Camera A')
        self.assertEqual(saved['camera']['livePath'], 'livecam1')

    def test_normal_edit_preserves_binding_but_changed_url_clears_it(self):
        original = self.controller.store.save({
            'id': 'usb1', 'name': 'USB', 'url': 'rtsp://127.0.0.1:8554/livecam1',
            'tasks': [], 'regions': [], 'cover': '/covers/old.jpg',
            'liveDeviceId': 'device1', 'liveDeviceName': 'USB', 'livePath': 'livecam1'})
        payload = {key: original[key] for key in ['id', 'name', 'url', 'tasks', 'regions']}
        payload['name'] = '修改名称'
        status, data = self.request('POST', '/api/cameras/save', payload)
        self.assertEqual(status, 200)
        self.assertEqual(data['camera']['liveDeviceId'], 'device1')
        with patch.object(self.controller, 'running', return_value=True):
            with self.assertRaisesRegex(ValueError, '停止'):
                self.controller.remove_live_camera('livecam1')
        payload.update(url='rtsp://127.0.0.1/another', coverToken='changed',
                       liveDeviceId='伪造设备', livePath='livecam1')
        self.controller.probes['changed'] = (payload['url'], 0)
        status, data = self.request('POST', '/api/cameras/save', payload)
        self.assertEqual(status, 200)
        self.assertNotIn('liveDeviceId', data['camera'])
        self.assertNotIn('livePath', data['camera'])

    def test_prepare_failure_does_not_save_camera(self):
        with patch.object(self.controller, 'control_live_streams'), \
                patch.object(camera_source, 'wait_live_ready', return_value=False), \
                patch.object(self.controller, 'probe') as probe:
            status, data = self.request('POST', '/api/cameras/live/prepare',
                                        {'deviceId': 'USB Camera A'})
        self.assertEqual(status, 400)
        self.assertIn('推流', data['error'])
        probe.assert_not_called()
        self.assertEqual(self.controller.store.cameras, [])

    def test_prepare_rejects_simulation_before_changing_mapping(self):
        with patch.object(self.controller, 'live_stream_status', return_value={
                'active': True, 'mode': 'lite'}):
            status, data = self.request('POST', '/api/cameras/live/prepare',
                                        {'deviceId': 'USB Camera A'})
        self.assertEqual(status, 400)
        self.assertIn('模拟流', data['error'])
        self.assertFalse((self.work/'mediamtx-live.yml').exists())

    def test_map_device_and_write_config(self):
        status, data = self.request('POST', '/api/cameras/live', {'device': 'USB Camera A'})
        self.assertEqual(status, 200)
        self.assertEqual(data['path'], 'livecam1')
        self.assertEqual(data['url'], 'rtsp://127.0.0.1:8554/livecam1')
        self.assertTrue(data['added'])
        config = self.work/'mediamtx-live.yml'
        self.assertTrue(config.is_file())
        text = config.read_text(encoding='utf-8')
        self.assertIn(camera_source.LIVE_PATH_MARK, text)
        self.assertIn('livecam1:', text)
        self.assertIn('video="USB Camera A"', text)
        # 重复接入复用原映射，不重复占用同一个物理设备。
        status, second = self.request('POST', '/api/cameras/live', {'device': 'USB Camera A'})
        self.assertEqual(second['path'], 'livecam1')

    def test_reject_unusable_or_unknown_device(self):
        for device in ('Virtual B', 'Not Exist', ''):
            status, data = self.request('POST', '/api/cameras/live', {'device': device})
            self.assertEqual(status, 400, device)
        status, data = self.request('POST', '/api/cameras/live', {'device': 'USB Camera A'})
        status2, data2 = self.request('POST', '/api/cameras/live',
                                      {'device': 'USB Camera A', 'path': 'Bad Name'})
        self.assertEqual(status, 200)
        self.assertEqual(status2, 400)

    def test_remove_live_path(self):
        self.request('POST', '/api/cameras/live', {'device': 'USB Camera A', 'path': 'mylive'})
        status, data = self.request('POST', '/api/cameras/live/remove', {'path': 'mylive'})
        self.assertEqual(status, 200)
        self.assertTrue(data['ok'])
        self.assertNotIn('mylive:', (self.work/'mediamtx-live.yml').read_text(encoding='utf-8'))
        status, _ = self.request('POST', '/api/cameras/live/remove', {'path': 'mylive'})
        self.assertEqual(status, 400)

    def test_stream_control_start_stop(self):
        fake = type('Result', (), {'returncode': 0, 'stdout': '已启动', 'stderr': ''})()
        with patch.object(console.subprocess, 'run', return_value=fake) as run:
            status, data = self.request('POST', '/api/cameras/live/streams', {'action': 'start'})
        self.assertEqual(status, 200)
        self.assertTrue(data['ok'])
        self.assertIn('start', run.call_args.args[0])
        self.assertIn('live', run.call_args.args[0])
        with patch.object(console.subprocess, 'run', return_value=fake):
            status, _ = self.request('POST', '/api/cameras/live/streams', {'action': 'stop'})
        self.assertEqual(status, 200)
        status, _ = self.request('POST', '/api/cameras/live/streams', {'action': 'restart'})
        self.assertEqual(status, 400)

    def test_stream_control_failure_is_surfaced(self):
        fake = type('Result', (), {'returncode': 3,
                                   'stdout': '', 'stderr': '端口已被其他程序占用'})()
        with patch.object(console.subprocess, 'run', return_value=fake):
            status, data = self.request('POST', '/api/cameras/live/streams', {'action': 'start'})
        self.assertEqual(status, 400)
        self.assertIn('端口', data['error'])

    def test_requires_token(self):
        status, _ = self.request('POST', '/api/cameras/live',
                                 {'device': 'USB Camera A'}, token='')
        self.assertEqual(status, 403)
        status, _ = self.request('POST', '/api/cameras/live/batch',
                                 {'selections': [], 'start': False}, token='')
        self.assertEqual(status, 403)

    def test_batch_api_saves_without_opening_device(self):
        status, data = self.request('POST', '/api/cameras/live/batch',
                                    {'selections': [{'deviceId': 'USB Camera A', 'model': 'opencv',
                                                     'events': ['brightness']}], 'start': False})
        self.assertEqual(status, 200)
        self.assertTrue(data['ok'])
        self.assertEqual(data['results'][0]['status'], 'saved')
        self.assertEqual(len(self.controller.store.cameras), 1)


if __name__ == '__main__':
    unittest.main()
