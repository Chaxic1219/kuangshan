"""检测选框统一样式：中文文案、报警/陪衬色、中文绘制。"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np

# 与前端 frame.mjs / 告警处置色板一致（BGR）
ALARM_BGR = (102, 128, 255)  # #ff8066
FOLLOW_BGR = (196, 234, 85)  # #55eac4
ALARM_HEX = '#ff8066'
FOLLOW_HEX = '#55eac4'

LABELS = {
    'person': '人员',
    'car': '汽车',
    'truck': '卡车',
    'bus': '公交车',
    'motorcycle': '摩托车',
    'bicycle': '自行车',
    'fire': '火焰',
    'smoke': '烟雾',
    'hardhat': '安全帽',
    'no-hardhat': '未戴安全帽',
    'safety vest': '反光衣',
    'no-safety vest': '未穿反光衣',
    'mask': '口罩',
    'no-mask': '未戴口罩',
    'vehicle': '车辆',
    'machinery': '机械',
    '过暗': '过暗',
    '过亮': '过亮',
}

_FONT_CANDIDATES = (
    '/System/Library/Fonts/PingFang.ttc',
    '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
    '/Library/Fonts/Arial Unicode.ttf',
    '/System/Library/Fonts/Hiragino Sans GB.ttc',
    'C:/Windows/Fonts/msyh.ttc',
    'C:/Windows/Fonts/msyh.ttf',
    'C:/Windows/Fonts/simhei.ttf',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
)


def detection_label(label) -> str:
    raw = '' if label is None else str(label)
    mapped = LABELS.get(raw.lower())
    if mapped:
        return mapped
    if any('\u4e00' <= ch <= '\u9fff' for ch in raw):
        return raw
    return raw


def format_detection_caption(detection: dict | None) -> str:
    item = detection if isinstance(detection, dict) else {}
    name = detection_label(item.get('label'))
    conf = item.get('confidence')
    if isinstance(conf, (int, float)) and math.isfinite(conf):
        return f'{name} {int(round(float(conf) * 100))}%'
    return name


def is_alarm_stroke(detection: dict | None) -> bool:
    """与规格一致：有 alarmable 则用之；否则看 inRegion；都无则默认报警色。"""
    item = detection if isinstance(detection, dict) else {}
    if 'alarmable' in item:
        return bool(item.get('alarmable'))
    if 'inRegion' in item:
        return bool(item.get('inRegion'))
    return True


def stroke_bgr(detection: dict | None) -> tuple[int, int, int]:
    return ALARM_BGR if is_alarm_stroke(detection) else FOLLOW_BGR


def stroke_hex(detection: dict | None) -> str:
    return ALARM_HEX if is_alarm_stroke(detection) else FOLLOW_HEX


@lru_cache(maxsize=1)
def resolve_cjk_font() -> str | None:
    for path in _FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return None


@lru_cache(maxsize=8)
def _pil_font(size: int):
    from PIL import ImageFont
    path = resolve_cjk_font()
    if not path:
        return ImageFont.load_default()
    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        return ImageFont.load_default()


def draw_caption(canvas, text: str, origin: tuple[int, int],
                 color_bgr: tuple[int, int, int], *, font_size: int = 16) -> None:
    """在 BGR 画布上绘制中文文案（原地修改）。无可用字体时尽量降级。"""
    if not text:
        return
    x, y = origin
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        cv2.putText(canvas, text, (x, max(12, y)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, color_bgr, 1, cv2.LINE_AA)
        return
    if resolve_cjk_font() is None:
        # 无中文字体时避免英文乱码占位：含汉字则跳过绘制
        if any('\u4e00' <= ch <= '\u9fff' for ch in text):
            return
        cv2.putText(canvas, text, (x, max(12, y)), cv2.FONT_HERSHEY_SIMPLEX,
                    0.5, color_bgr, 1, cv2.LINE_AA)
        return
    rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    font = _pil_font(max(12, int(font_size)))
    fill = (int(color_bgr[2]), int(color_bgr[1]), int(color_bgr[0]))
    # origin 为基线附近；Pillow 用左上角，略上移
    top = max(0, y - font_size)
    draw.text((x, top), text, font=font, fill=fill)
    canvas[:] = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
