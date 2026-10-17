"""多路接入使用隔离目录；模拟硬件、进程边界，不打开真实摄像头。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import camera_source
import console
from catalog import Store


class LiveSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.config = self.work/'live.yml'
        self.config.write_text('rtspAddress: :20000\npaths:\n', encoding='utf-8')
        self.devices = [{'id': '@first', 'name': 'USB Camera', 'usable': True},
                        {'id': '@second', 'name': 'USB Camera', 'usable': True}]
        self.c = console.Controller(self.work)
        self.addCleanup(self.c.close)
        for item in (
            patch.object(camera_source, 'LIVE_CONFIG', self.config),
            patch.object(camera_source, 'list_camera_devices', side_effect=lambda **kw: (self.devices, '')),
            patch.object(camera_source, 'wait_live_ready', return_value=True, create=True),
            patch.object(console, 'model_status', return_value=[{'id': 'fire', 'ready': True}, {'id': 'ppe', 'ready': True}]),
            patch.object(self.c, 'live_stream_status', return_value={'running': False, 'active': False, 'mode': ''}),
        ):
            item.start()
            self.addCleanup(item.stop)

    def select(self, device='@first', model='fire', events=None):
        return {'deviceId': device, 'model': model, 'events': events or ['fire']}

    def configure(self, selections, start=False):
        self.assertTrue(hasattr(self.c, 'configure_live_cameras'), '缺少批量接入接口')
        return self.c.configure_live_cameras(selections, start)

    def test_same_name_devices_and_repeat_save(self):
        rows = [self.select(), self.select('@second', 'ppe', ['no_helmet', 'no_vest'])]
        first = self.configure(rows)
        again = self.configure(rows)
        self.assertTrue(first['ok'])
        self.assertEqual([r['cameraId'] for r in first['results']], [r['cameraId'] for r in again['results']])
        self.assertEqual(len(Store(self.work).cameras), 2)
        mapping = camera_source.managed_paths(self.config)
        self.assertEqual(len(mapping), 2)
        self.assertEqual(set(mapping.values()), {'@first', '@second'})
        config_text = self.config.read_text(encoding='utf-8')
        self.assertIn('video="USB Camera"', config_text, '采集命令必须使用友好名')
        self.assertNotIn('video="@device', config_text, '唯一标识不能写入 ffmpeg 命令')
        for c in self.c.store.cameras:
            self.assertEqual(len({t['model'] for t in c['tasks']}), 1)
            self.assertIn(':20000/', c['url'])

    def test_reject_cross_model_events_before_writing(self):
        with self.assertRaises(ValueError):
            self.configure([self.select(events=['fire', 'no_helmet'])])
        self.assertFalse(self.c.store.cameras)
        self.assertFalse(camera_source.managed_paths(self.config))

    def test_offline_saved_device_remains_visible(self):
        self.configure([self.select()])
        self.devices.clear()
        data = self.c.camera_devices()
        saved = data['devices'][0]
        self.assertEqual(saved['id'], '@first')
        self.assertFalse(saved['connected'])
        self.assertEqual(saved['selection']['model'], 'fire')

    def test_partial_failure_does_not_block_other_camera(self):
        with patch.object(self.c, 'control_live_streams') as streams, \
                patch.object(self.c, 'probe', side_effect=[ValueError('设备被占用'), {'cover': '/covers/ok.jpg'}]), \
                patch.object(self.c, 'start') as start:
            result = self.configure([self.select(), self.select('@second')], True)
        self.assertFalse(result['ok'])
        self.assertFalse(result['results'][0]['ok'])
        self.assertTrue(result['results'][1]['ok'])
        streams.assert_called_once_with('start')
        start.assert_called_once_with(result['results'][1]['cameraId'])
        self.assertEqual(len(self.c.store.cameras), 2)

    def test_port_open_without_ready_path_does_not_start_analysis(self):
        with patch.object(camera_source, 'wait_live_ready', return_value=False, create=True), \
                patch.object(self.c, 'control_live_streams'), \
                patch.object(self.c, 'probe', return_value={'cover': '/covers/ok.jpg'}) as probe, \
                patch.object(self.c, 'start') as start:
            result = self.configure([self.select()], True)
        self.assertFalse(result['ok'])
        probe.assert_not_called()
        start.assert_not_called()

    def test_capacity_checked_before_writing(self):
        with patch.object(self.c, 'running_count', return_value=console.MAX_RUNNING):
            with self.assertRaises(ValueError):
                self.configure([self.select()], True)
        self.assertFalse(self.c.store.cameras)

    def test_simulation_mode_is_not_stopped_or_modified(self):
        self.c.live_stream_status.return_value = {'running': False, 'active': True, 'mode': 'lite'}
        before = self.config.read_text(encoding='utf-8')
        with patch.object(self.c, 'control_live_streams') as streams:
            with self.assertRaises(ValueError):
                self.configure([self.select()], True)
        streams.assert_not_called()
        self.assertEqual(self.config.read_text(encoding='utf-8'), before)

    def test_running_camera_is_not_restarted(self):
        self.configure([self.select()])
        self.c.live_stream_status.return_value = {'running': True, 'active': True, 'mode': 'live'}
        with patch.object(self.c, 'running', return_value=True), \
                patch.object(self.c, 'start') as start, patch.object(self.c, 'probe') as probe:
            result = self.configure([self.select()], True)
        self.assertTrue(result['ok'])
        start.assert_not_called()
        probe.assert_not_called()

    def test_changing_model_of_running_camera_rejected(self):
        self.configure([self.select()])
        with patch.object(self.c, 'running', return_value=True):
            with self.assertRaises(ValueError):
                self.configure([self.select(model='ppe', events=['no_helmet'])])
        self.assertEqual(self.c.store.cameras[0]['tasks'][0]['model'], 'fire')

    def test_legacy_api_returns_configured_port_and_reuses_device(self):
        # 同名设备不能通过显示名称猜选其中一台。
        with self.assertRaises(ValueError):
            self.c.save_live_camera('USB Camera')

    def test_legacy_api_unique_id_uses_configured_port(self):
        first = self.c.save_live_camera('@first')
        again = self.c.save_live_camera('@first')
        self.assertEqual(first['path'], again['path'])
        self.assertIn(':20000/', first['url'])

    def test_existing_manual_video_is_adopted_without_duplicate_capture(self):
        self.devices[:] = self.devices[:1]
        camera_source.upsert_live_path(self.config, 'livecam1', 'USB Camera')
        old = self.c.store.save({'id': 'existing', 'name': '已有摄像头', 'location': '',
                                 'url': 'rtsp://127.0.0.1:20000/livecam1',
                                 'tasks': [], 'regions': [], 'cover': '/covers/old.jpg'})
        result = self.configure([self.select()])
        self.assertEqual(result['results'][0]['cameraId'], old['id'])
        self.assertEqual(len(self.c.store.cameras), 1)
        self.assertEqual(camera_source.managed_paths(self.config), {'livecam1': '@first'})
        config_text = self.config.read_text(encoding='utf-8')
        self.assertIn('video="USB Camera"', config_text, '采集命令必须使用友好名')
        self.assertNotIn('video="@device', config_text, '唯一标识不能写进 ffmpeg 命令')

    def draft_camera(self, **overrides):
        camera = {'id': '', 'name': '本机摄像头', 'location': '东区',
                  'tasks': [{'eventId': 'fire', 'confidence': .5, 'interval': 1,
                             'cooldown': 10, 'enabled': True, 'regionIds': []}],
                  'regions': []}
        camera.update(overrides)
        return camera

    def test_adopt_creates_live_camera_with_cover(self):
        with patch.object(self.c, 'control_live_streams') as streams, \
                patch.object(self.c, 'probe', return_value={'cover': '/covers/live.jpg'}) as probe:
            result = self.c.adopt_live_camera('@first', self.draft_camera())
        self.assertTrue(result['ok'], result)
        self.assertIn('livecam1', result['url'])
        cameras = Store(self.work).cameras
        self.assertEqual(len(cameras), 1)
        self.assertEqual(cameras[0]['liveDeviceId'], '@first')
        self.assertEqual(cameras[0]['tasks'][0]['eventId'], 'fire')
        self.assertEqual(cameras[0]['cover'], '/covers/live.jpg')
        self.assertEqual(camera_source.managed_paths(self.config), {'livecam1': '@first'})
        config_text = self.config.read_text(encoding='utf-8')
        self.assertIn('video="USB Camera"', config_text, '采集命令必须使用友好名')
        self.assertNotIn('video="@device', config_text, '唯一标识不能写进 ffmpeg 命令')
        streams.assert_called_once_with('start')
        probe.assert_called_once()

    def test_adopt_reuses_camera_and_mapping(self):
        with patch.object(self.c, 'control_live_streams'), \
                patch.object(self.c, 'probe', return_value={'cover': '/covers/live.jpg'}):
            first = self.c.adopt_live_camera('@first', self.draft_camera())
            second = self.c.adopt_live_camera('@first', self.draft_camera(name='改名后'))
        self.assertEqual(first['cameraId'], second['cameraId'])
        self.assertEqual(len(Store(self.work).cameras), 1)
        self.assertEqual(Store(self.work).cameras[0]['name'], '改名后')
        self.assertEqual(camera_source.managed_paths(self.config), {'livecam1': '@first'})
        config_text = self.config.read_text(encoding='utf-8')
        self.assertIn('video="USB Camera"', config_text, '采集命令必须使用友好名')
        self.assertNotIn('video="@device', config_text, '唯一标识不能写进 ffmpeg 命令')

    def test_adopt_rejects_change_while_running(self):
        with patch.object(self.c, 'control_live_streams'), \
                patch.object(self.c, 'probe', return_value={'cover': '/covers/live.jpg'}):
            self.c.adopt_live_camera('@first', self.draft_camera())
        with patch.object(self.c, 'running', return_value=True):
            with self.assertRaises(ValueError):
                self.c.adopt_live_camera('@first', self.draft_camera(
                    tasks=[{'eventId': 'no_helmet', 'confidence': .5, 'interval': 1,
                            'cooldown': 10, 'enabled': True, 'regionIds': []}]))
        self.assertEqual(Store(self.work).cameras[0]['tasks'][0]['eventId'], 'fire')

    def test_adopt_offline_device_keeps_config_without_stream(self):
        with patch.object(self.c, 'control_live_streams'), \
                patch.object(self.c, 'probe', return_value={'cover': '/covers/live.jpg'}):
            self.c.adopt_live_camera('@first', self.draft_camera())
        self.devices.clear()
        with patch.object(self.c, 'control_live_streams') as streams, \
                patch.object(self.c, 'probe') as probe:
            result = self.c.adopt_live_camera('@first', self.draft_camera(name='离线改名'))
        self.assertTrue(result['ok'])
        self.assertIn('离线', result['message'])
        streams.assert_not_called()
        probe.assert_not_called()
        self.assertEqual(Store(self.work).cameras[0]['name'], '离线改名')

    def test_adopt_requires_tasks_and_device(self):
        with self.assertRaises(ValueError):
            self.c.adopt_live_camera('@first', self.draft_camera(tasks=[]))
        with self.assertRaises(ValueError):
            self.c.adopt_live_camera('', self.draft_camera())

    def test_stop_command_rejects_other_mode(self):
        from live_cameras import stream_manager
        manager = stream_manager()
        with patch.object(manager, 'read_session', return_value={'mode': 'lite', 'managerPid': 1}), \
                patch.object(manager, 'identity_problem', return_value=None), \
                patch.object(manager, 'wait_until') as wait:
            self.assertIn('expected_mode', __import__('inspect').signature(manager.command_stop).parameters)
            self.assertEqual(manager.command_stop(expected_mode='live'), 4)
        wait.assert_not_called()

    def test_stream_manager_port_matches_quoted_config(self):
        from live_cameras import stream_manager
        self.config.write_text('rtspAddress: "127.0.0.1:20000"\npaths:\n', encoding='utf-8')
        self.assertEqual(stream_manager().rtsp_port('live', self.config), 20000)

    def test_remove_running_device_is_rejected(self):
        self.configure([self.select()])
        path = self.c.store.cameras[0]['livePath']
        with patch.object(self.c, 'running', return_value=True):
            with self.assertRaises(ValueError):
                self.c.remove_live_camera(path)
        self.assertIn(path, camera_source.managed_paths(self.config))

    def test_live_status_does_not_confuse_simulation_or_reused_pid(self):
        from live_cameras import stream_manager
        with patch.object(console, 'read_json', return_value={'managerPid': 1, 'mode': 'lite'}), \
                patch.object(stream_manager(), 'identity_problem', return_value=None):
            self.assertFalse(console.Controller.live_stream_status(self.c)['running'])
        with patch.object(console, 'read_json', return_value={'managerPid': 1, 'mode': 'live'}), \
                patch.object(stream_manager(), 'identity_problem', return_value='身份不符'):
            self.assertFalse(console.Controller.live_stream_status(self.c)['running'])


if __name__ == '__main__':
    unittest.main()
