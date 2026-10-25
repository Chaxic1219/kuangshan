"""hostplat 摄像头解析与推流命令（不依赖本机设备）。"""
import unittest
from unittest.mock import patch

from hostplat import camera as host_camera


AVF_SAMPLE = """
[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] FaceTime HD Camera
[AVFoundation indev @ 0x1] [1] USB Camera
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] MacBook Pro Microphone
"""


class AvfoundationTests(unittest.TestCase):
    def test_parse_avfoundation_devices(self):
        devices = host_camera.parse_device_list(AVF_SAMPLE, backend='avfoundation')
        self.assertEqual([d['name'] for d in devices], ['FaceTime HD Camera', 'USB Camera'])
        self.assertEqual(devices[0]['index'], '0')
        self.assertEqual(devices[0]['id'], 'avfoundation:0')
        self.assertTrue(devices[0]['usable'])

    def test_push_command_avfoundation(self):
        with patch.object(host_camera, 'capture_backend', return_value='avfoundation'), \
                patch.object(host_camera, 'ffmpeg_bin_for_runoninit', return_value='"bin/darwin/ffmpeg"'):
            command = host_camera.build_push_command('0', 'livecam1', framerate=30)
        self.assertIn('-f avfoundation', command)
        self.assertIn('-i "0"', command)
        self.assertIn('rtsp://127.0.0.1:8554/livecam1', command)
        self.assertNotIn('.exe', command)


if __name__ == '__main__':
    unittest.main()
