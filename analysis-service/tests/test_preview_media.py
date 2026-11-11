"""监控预览：本地文件走 /preview，本机 MediaMTX RTSP 走 WebRTC。"""
import unittest

import console


class PreviewMediaTests(unittest.TestCase):
    def test_local_file_uses_http_video_preview(self):
        preview = console.preview_for({
            'id': 'abc',
            'url': 'kstool/mediamtx/videos/camera12.mp4',
        })
        self.assertEqual(preview['kind'], 'video')
        self.assertEqual(preview['url'], '/preview/abc')

    def test_local_mediamtx_rtsp_uses_webrtc(self):
        preview = console.preview_for({
            'id': 'usb1',
            'url': 'rtsp://127.0.0.1:8554/livecam1',
        })
        self.assertEqual(preview['kind'], 'webrtc')
        self.assertEqual(preview['path'], 'livecam1')
        self.assertTrue(preview['url'].endswith('/livecam1'))
        self.assertTrue(preview['whep'].endswith('/livecam1/whep'))

    def test_remote_rtsp_stays_frame_preview(self):
        preview = console.preview_for({
            'id': 'cam',
            'url': 'rtsp://10.0.0.8:554/stream1',
        })
        self.assertEqual(preview['kind'], 'frames')


if __name__ == '__main__':
    unittest.main()
