"""验收脚本纯函数测试：分位数与隔离模拟流配置（不启动进程）。"""
import tempfile
import unittest
from pathlib import Path

import acceptance


class AcceptanceHelperTests(unittest.TestCase):
    def test_percentile(self):
        self.assertIsNone(acceptance.percentile([], 0.95))
        self.assertEqual(acceptance.percentile([1.0], 0.95), 1.0)
        self.assertEqual(acceptance.percentile([0.5, 1.0, 2.0, 3.0, 4.0], 0.95), 4.0)
        self.assertEqual(acceptance.percentile([1, 2, 3, 4], 0.5), 2)
        self.assertEqual(acceptance.percentile([1, 2, 3, 4], 0.5), 2)

    def test_isolated_stream_config_rewrites_push_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, rtsp = acceptance.isolated_stream_config(Path(tmp))
            text = path.read_text(encoding='utf-8')
            self.assertIn('rtspAddress: 127.0.0.1:%d' % rtsp, text)
            self.assertEqual(text.count('rtspAddress: 127.0.0.1:%d' % rtsp), 1)
            self.assertNotIn('localhost:8554', text, 'runOnInit 推流目标必须同步到隔离端口')
            pushes = text.count('rtsp://127.0.0.1:%d/' % rtsp)
            self.assertGreaterEqual(pushes, 8, '至少 8 路推流地址应指向隔离端口')
            for key in ('webrtcLocalUDPAddress', 'moqHTTP2Address', 'apiAddress', 'hlsAddress'):
                if key in text:
                    self.assertIn('%s: 127.0.0.1:' % key, text)


if __name__ == '__main__':
    unittest.main()
