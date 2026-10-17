"""区域配置与进入状态。"""
import math
import cv2
import numpy as np


def validate_polygon(points):
    if not isinstance(points, list) or not 3 <= len(points) <= 64:
        raise ValueError('区域至少需要 3 个顶点，最多 64 个')
    for p in points:
        if not isinstance(p, list) or len(p) != 2 or not all(
                type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 1 for v in p):
            raise ValueError('区域顶点须位于画面内')
    if len({tuple(p) for p in points}) != len(points):
        raise ValueError('区域顶点不能重复')
    if abs(cv2.contourArea(np.array(points, dtype=np.float32))) < .0001:
        raise ValueError('区域面积过小或顶点共线')

    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

    def on(a, b, c):
        return abs(cross(a, b, c)) < 1e-9 and all(
            min(a[i], b[i])-1e-9 <= c[i] <= max(a[i], b[i])+1e-9 for i in (0, 1))

    n = len(points)
    for i in range(n):
        a, b = points[i], points[(i+1) % n]
        for j in range(i+1, n):
            if j == i+1 or (i == 0 and j == n-1):
                continue
            c, d = points[j], points[(j+1) % n]
            if (cross(a, b, c)*cross(a, b, d) < 0 and cross(c, d, a)*cross(c, d, b) < 0) or \
                    on(a, b, c) or on(a, b, d) or on(c, d, a) or on(c, d, b):
                raise ValueError('区域边线不能交叉或重叠')
    return points


def contains(points, bbox):
    point = ((bbox[0] + bbox[2]) / 2, bbox[3])
    return cv2.pointPolygonTest(np.array(points, dtype=np.float32), point, False) >= 0


class EntryState:
    def __init__(self):
        self.inside = set()
        self.missing = {}

    def update(self, detections, zones):
        entered, visible = [], set()
        for detection in detections:
            tid = detection.get('trackId')
            if tid is None:
                continue
            for zone in zones:
                if not zone.get('enabled', True):
                    continue
                key = (zone['id'], tid)
                visible.add(key)
                self.missing.pop(key, None)
                if contains(zone['points'], detection['bbox']):
                    if key not in self.inside:
                        entered.append({**detection, 'regionId': zone['id'], 'regionName': zone['name']})
                    self.inside.add(key)
                else:
                    self.inside.discard(key)
        # 短时漏检保留状态；连续 3 次缺失后允许重新识别进入。
        for key in self.inside - visible:
            self.missing[key] = self.missing.get(key, 0) + 1
            if self.missing[key] >= 3:
                self.inside.discard(key)
                self.missing.pop(key, None)
        return entered
