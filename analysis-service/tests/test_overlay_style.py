"""检测选框统一样式：文案、颜色、中文绘制。"""
import unittest

import numpy as np

from overlay_style import (
    ALARM_BGR, FOLLOW_BGR, detection_label, draw_caption,
    format_detection_caption, is_alarm_stroke, resolve_cjk_font, stroke_bgr,
)


class OverlayStyleTests(unittest.TestCase):
    def test_chinese_caption_with_confidence(self):
        self.assertEqual(detection_label('NO-Hardhat'), '未戴安全帽')
        self.assertEqual(
            format_detection_caption({'label': 'NO-Hardhat', 'confidence': 0.9}),
            '未戴安全帽 90%')
        self.assertEqual(
            format_detection_caption({'label': 'hardhat', 'confidence': 0.87}),
            '安全帽 87%')
        self.assertEqual(format_detection_caption({'label': '过暗'}), '过暗')

    def test_stroke_prefers_alarmable_then_region_then_default(self):
        self.assertTrue(is_alarm_stroke({'alarmable': True, 'inRegion': False}))
        self.assertFalse(is_alarm_stroke({'alarmable': False, 'inRegion': True}))
        self.assertTrue(is_alarm_stroke({'inRegion': True}))
        self.assertFalse(is_alarm_stroke({'inRegion': False}))
        self.assertTrue(is_alarm_stroke({}))
        self.assertEqual(stroke_bgr({'alarmable': True}), ALARM_BGR)
        self.assertEqual(stroke_bgr({'alarmable': False}), FOLLOW_BGR)

    def test_draw_caption_changes_pixels_when_font_available(self):
        if resolve_cjk_font() is None:
            self.skipTest('本机无可用中文字体')
        canvas = np.zeros((80, 200, 3), dtype=np.uint8)
        before = int(canvas.sum())
        draw_caption(canvas, '未戴安全帽 90%', (8, 40), ALARM_BGR, font_size=18)
        self.assertGreater(int(canvas.sum()), before)


if __name__ == '__main__':
    unittest.main()
