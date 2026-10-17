"""批次 4：分析进程资源采样（预热、有效样本、空值语义）。"""
import time
import unittest
from unittest.mock import Mock

import service


class ResourceSamplerTests(unittest.TestCase):
    def test_unavailable_before_first_valid_interval(self):
        sampler = service.ResourceSampler()
        sample = sampler.sample()
        self.assertFalse(sample['available'])
        self.assertIsNone(sample['cpuPercent'], '首个有效间隔前不得用假零值')
        self.assertIsNone(sample['rssMb'])
        self.assertIn('sampledAt', sample)

    def test_positive_cpu_under_load_and_lower_when_idle(self):
        sampler = service.ResourceSampler()
        time.sleep(1.05)  # 越过预热期
        deadline = time.monotonic() + 1.2
        while time.monotonic() < deadline:
            pass  # 保持一个逻辑核忙碌
        busy = sampler.sample()
        self.assertTrue(busy['available'], '预热与有效间隔后应得到有效样本')
        self.assertIsNotNone(busy['cpuPercent'])
        self.assertGreater(busy['cpuPercent'], 0, '计算负载下进程 CPU 应大于 0')
        self.assertIsNotNone(busy['rssMb'])

        time.sleep(1.2)
        idle = sampler.sample()
        self.assertTrue(idle['available'])
        self.assertLess(idle['cpuPercent'], busy['cpuPercent'], '空闲后 CPU 采样应下降')

    def test_recent_sample_is_reused(self):
        sampler = service.ResourceSampler()
        time.sleep(1.05)
        first = sampler.sample()
        self.assertTrue(first['available'])
        second = sampler.sample()
        self.assertEqual(first['sampledAt'], second['sampledAt'], '1 秒内应复用最近完整样本')

    def test_error_returns_unavailable(self):
        sampler = service.ResourceSampler()
        sampler._process = Mock()
        sampler._process.oneshot.side_effect = RuntimeError('采样失败')
        time.sleep(1.05)
        sample = sampler.sample()
        self.assertFalse(sample['available'])
        self.assertIsNone(sample['cpuPercent'])
        self.assertIsNone(sample['rssMb'])


if __name__ == '__main__':
    unittest.main()
