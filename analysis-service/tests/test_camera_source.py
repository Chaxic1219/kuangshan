"""摄像头映射：dshow 设备枚举与 MediaMTX 配置生成（不启动进程的部分）。"""
import tempfile
import io
import unittest
from pathlib import Path
from unittest.mock import patch
import camera_source

from camera_source import (build_push_command, ensure_live_config, list_camera_devices,
                           managed_paths, remove_live_path, upsert_live_path,
                           LIVE_END_MARK, LIVE_PATH_MARK, write_live_config)


class FakeFFprobeExit:
    """模拟 ffmpeg -list_devices 的输出：输出走 stderr，退出码为 1。"""

    def __init__(self, text):
        self.text = text
        self.stderr = text
        self.stdout = ''
        self.returncode = 1


class CommandTests(unittest.TestCase):
    def test_push_command_uses_dshow_and_rtsp(self):
        command = build_push_command(
            'USB2.0 PC CAMERA', 'livecam_usb1', framerate=25, size='1280x720')
        self.assertIn('-f dshow', command)
        self.assertIn('-rtbufsize 100M', command, 'dshow 默认缓冲过小会丢帧')
        self.assertIn('video="USB2.0 PC CAMERA"', command)
        self.assertIn('-framerate 25', command)
        self.assertIn('-video_size 1280x720', command)
        self.assertIn('libx264', command)
        self.assertIn('-bf 0', command)
        self.assertIn('-an', command)
        self.assertIn('rtsp://127.0.0.1:8554/livecam_usb1', command)
        self.assertNotIn('-c copy', command)

    def test_push_command_omits_video_size_by_default(self):
        """不指定分辨率时跟随设备原生格式（UVC 摄像头支持的分辨率并不统一）。"""
        command = build_push_command('USB2.0 PC CAMERA', 'livecam_usb1')
        self.assertNotIn('-video_size', command)
        self.assertNotIn('-framerate', command)

    def test_push_command_quotes_device_name(self):
        command = build_push_command('Logi C920 (HD)', 'cam')
        self.assertIn('video="Logi C920 (HD)"', command)


class DeviceListTests(unittest.TestCase):
    def run_parse(self, text):
        with patch('camera_source.run_ffmpeg', return_value=FakeFFprobeExit(text)):
            return list_camera_devices()

    def test_parse_devices(self):
        text = (
            '[in#0 @ 000001e7ccff1840] "USB2.0 PC CAMERA" (video)\n'
            '[in#0 @ 000001e7ccff1840]   Alternative name "@device_pnp_abc"\n'
            '[in#0 @ 000001e7ccff1840] "ToDesk Camera" (video)\n'
            '[in#0 @ 000001e7ccff1840]   Alternative name "@device_sw_{860}"\n'
            '[in#0 @ 000001e7ccff1840] "OBS Virtual Camera" (none)\n'
            '[in#0 @ 000001e7ccff1840]   Alternative name "@device_sw_{a3f}"\n'
            '[in#0 @ 000001e7ccff1840] "\u9ea6\u514b\u98ce (Realtek)" (audio)\n'
            '[in#0 @ 000001e7ccff1840]   Alternative name "@device_cm_x"\n')
        devices = self.run_parse(text)
        names = [device['name'] for device in devices]
        self.assertIn('USB2.0 PC CAMERA', names)
        self.assertIn('ToDesk Camera', names)
        # "(none)" 是 ffmpeg 对无数据流虚拟摄像头的标注，跳过但记录 unknown
        self.assertNotIn('OBS Virtual Camera', [d['name'] for d in devices if d['usable']])
        self.assertIn('OBS Virtual Camera', names)
        # 音频设备不进入摄像头列表
        self.assertNotIn('\u9ea6\u514b\u98ce (Realtek)', names)

    def test_parse_failure_returns_empty_with_error(self):
        with patch('camera_source.run_ffmpeg', side_effect=OSError('ffmpeg 不可用')):
            devices, error = list_camera_devices(with_error=True)
        self.assertEqual(devices, [])
        self.assertTrue(error)

    def test_same_name_devices_keep_distinct_identifiers(self):
        devices = self.run_parse(
            '[dshow @ x] "USB Camera" (video)\n'
            '[dshow @ x]   Alternative name "@device_pnp_first"\n'
            '[dshow @ x] "USB Camera" (video)\n'
            '[dshow @ x]   Alternative name "@device_pnp_second"\n')
        self.assertEqual([d.get('id') for d in devices],
                         ['@device_pnp_first', '@device_pnp_second'])

    def test_ambiguous_devices_are_not_selectable(self):
        devices = self.run_parse('[dshow @ x] "USB" (video)\n[dshow @ x] "USB" (video)\n')
        self.assertTrue(all(not d['usable'] for d in devices))


class ConfigUpsertTests(unittest.TestCase):
    def test_batch_collision_does_not_write_first_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'m.yml'
            before = 'paths:\n  manual:\n    source: publisher\n'
            config.write_text(before, encoding='utf-8')
            with self.assertRaises(ValueError):
                write_live_config(config, {'livecam1': 'Camera A', 'manual': 'Camera B'})
            self.assertEqual(config.read_text(encoding='utf-8'), before)

    def test_ready_check_uses_api_port_and_requires_ready_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'m.yml'
            config.write_text('api: yes\napiAddress: "127.0.0.1:20001"\npaths:\n', encoding='utf-8')
            with patch.object(camera_source, 'LIVE_CONFIG', config), \
                    patch.object(camera_source, 'urlopen', return_value=io.BytesIO(b'{"ready":true}')) as request:
                self.assertTrue(camera_source.wait_live_ready('livecam1', timeout=.05))
                self.assertEqual(request.call_args.args[0], 'http://127.0.0.1:20001/v3/paths/get/livecam1')
            with patch.object(camera_source, 'LIVE_CONFIG', config), \
                    patch.object(camera_source, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(b'{"ready":false}')):
                self.assertFalse(camera_source.wait_live_ready('livecam1', timeout=.01))
    def test_manual_collision_is_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'m.yml'
            before = 'paths:\n  livecam1:\n    source: publisher\n'
            config.write_text(before, encoding='utf-8')
            with self.assertRaises(ValueError):
                upsert_live_path(config, 'livecam1', 'Camera A')
            self.assertEqual(config.read_text(encoding='utf-8'), before)

    def test_incomplete_markers_reject_without_losing_manual_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'m.yml'
            before = 'paths:\n' + LIVE_PATH_MARK + '\n  manual:\n    source: publisher\n'
            config.write_text(before, encoding='utf-8')
            with self.assertRaises(ValueError):
                upsert_live_path(config, 'livecam1', 'Camera A')
            self.assertEqual(config.read_text(encoding='utf-8'), before)

    def test_managed_paths_insert_under_paths_before_other_settings(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'m.yml'
            config.write_text('paths:\n  manual:\n    source: publisher\nlogLevel: info\n', encoding='utf-8')
            upsert_live_path(config, 'livecam1', 'Camera A')
            text = config.read_text(encoding='utf-8')
            self.assertLess(text.index(LIVE_END_MARK), text.index('logLevel:'))

    def write_base(self, folder):
        config = folder/'mediamtx-live.yml'
        config.write_text(
            'paths:\n'
            '  camera1:\n'
            '    runOnInit: >-\n'
            '      "bin/ffmpeg_bin/ffmpeg.exe" -re -stream_loop -1 -i\n'
            '      "videos/camera1.mp4"\n'
            '      -c copy -f rtsp rtsp://localhost:8554/camera1\n'
            '    runOnInitRestart: yes\n',
            encoding='utf-8')
        return config

    def test_upsert_appends_managed_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.write_base(Path(tmp))
            added = upsert_live_path(config, 'livecam_usb1', 'USB2.0 PC CAMERA')
            self.assertTrue(added)
            text = config.read_text(encoding='utf-8')
            self.assertIn(LIVE_PATH_MARK, text)
            self.assertIn('livecam_usb1:', text)
            self.assertIn('video="USB2.0 PC CAMERA"', text)
            # 原有 path 不受影响
            self.assertIn('camera1:', text)
            self.assertIn('videos/camera1.mp4', text)

    def test_upsert_replaces_existing_path_and_leaves_others(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.write_base(Path(tmp))
            upsert_live_path(config, 'livecam_usb1', 'Old Camera')
            added = upsert_live_path(config, 'livecam_usb1', 'New Camera')
            self.assertFalse(added)
            text = config.read_text(encoding='utf-8')
            self.assertEqual(text.count(LIVE_PATH_MARK), 1)
            self.assertNotIn('Old Camera', text)
            self.assertIn('New Camera', text)
            self.assertIn('camera1:', text)

    def test_upsert_keeps_manual_paths_after_managed_block(self):
        """受管段之后的用户 path 不能被覆盖或删除。"""
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'m.yml'
            config.write_text(
                'paths:\n'
                '  camera1:\n'
                '    runOnInit: >-\n'
                '      ffmpeg -re -i videos/camera1.mp4 -c copy -f rtsp rtsp://localhost:8554/camera1\n'
                '    runOnInitRestart: yes\n',
                encoding='utf-8')
            upsert_live_path(config, 'livecam_usb1', 'USB2.0 PC CAMERA')
            # 用户随后在受管段之后手写了一个 path
            after = config.read_text(encoding='utf-8')
            after += ('\n  manual1:\n'
                      '    runOnInit: >-\n'
                      '      ffmpeg -re -i videos/camera2.mp4 -c copy -f rtsp rtsp://localhost:8554/manual1\n'
                      '    runOnInitRestart: yes\n')
            config.write_text(after, encoding='utf-8')
            upsert_live_path(config, 'livecam_usb1', 'New Camera')
            text = config.read_text(encoding='utf-8')
            self.assertIn('manual1:', text, '手写 path 必须保留')
            self.assertIn('videos/camera2.mp4', text)
            self.assertIn('camera1:', text)
            self.assertIn('New Camera', text)
            self.assertEqual(text.count(LIVE_PATH_MARK), 1)
            self.assertEqual(text.count(LIVE_END_MARK), 1)
            self.assertLess(text.index(LIVE_PATH_MARK), text.index(LIVE_END_MARK))

    def test_multiple_paths_supported(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'m.yml'
            config.write_text('paths:\n', encoding='utf-8')
            upsert_live_path(config, 'livecam1', 'Camera A')
            upsert_live_path(config, 'livecam2', 'Camera B')
            mapping = managed_paths(config)
            self.assertEqual(mapping, {'livecam1': 'Camera A', 'livecam2': 'Camera B'})
            text = config.read_text(encoding='utf-8')
            self.assertIn('livecam1:', text)
            self.assertIn('livecam2:', text)

    def test_remove_live_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'m.yml'
            config.write_text('paths:\n', encoding='utf-8')
            upsert_live_path(config, 'livecam1', 'Camera A')
            upsert_live_path(config, 'livecam2', 'Camera B')
            self.assertTrue(remove_live_path(config, 'livecam1'))
            self.assertFalse(remove_live_path(config, 'missing'))
            mapping = managed_paths(config)
            self.assertEqual(mapping, {'livecam2': 'Camera B'})

    def test_ensure_live_config_uses_listener_template_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'live.yml'
            created = ensure_live_config(config)
            self.assertEqual(created, config)
            text = config.read_text(encoding='utf-8')
            self.assertIn('rtspAddress:', text, '沿用 lite 的监听设置')
            self.assertIn('paths:', text)
            self.assertIn('hls: no', text, 'live 模式关闭 HLS，避开 8888 端口冲突')
            self.assertNotIn('hls: yes', text)
            self.assertIn(LIVE_PATH_MARK, text)
            self.assertIn(LIVE_END_MARK, text)
            self.assertNotIn('videos/camera1.mp4', text, 'live 配置不得包含模拟流')
            # 已存在时不覆盖
            config.write_text('paths:\n  keep:\n', encoding='utf-8')
            ensure_live_config(config)
            self.assertIn('keep:', config.read_text(encoding='utf-8'))

    def test_push_command_follows_config_rtsp_port(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'m.yml'
            config.write_text('rtspAddress: 127.0.0.1:20000\npaths:\n', encoding='utf-8')
            upsert_live_path(config, 'livecam9', 'Camera X')
            text = config.read_text(encoding='utf-8')
            self.assertIn('rtsp://127.0.0.1:20000/livecam9', text)
            self.assertNotIn(':8554/', text)

    def test_upsert_rejects_invalid_path_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.write_base(Path(tmp))
            for bad in ('bad name', 'UPPER', 'cam/1', '', 'x' * 65):
                with self.assertRaises(ValueError):
                    upsert_live_path(config, bad, 'USB2.0 PC CAMERA')


if __name__ == '__main__':
    unittest.main()
