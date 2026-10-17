"""本地视频平台的配置边界和区域报警语义。"""
import copy
import tempfile
import unittest
from pathlib import Path
import catalog
import regions


POLYGON = [[.2, .2], [.8, .2], [.8, .8], [.2, .8]]


class LocalPlatformTests(unittest.TestCase):
    def test_dynamic_cameras_and_persistence(self):
        self.assertTrue(hasattr(catalog, 'Store'), '需要持久化动态视频目录')
        with tempfile.TemporaryDirectory() as folder:
            store = catalog.Store(Path(folder))
            for i in range(4):
                store.save({'id': '', 'name': f'视频{i}', 'url': f'rtsp://127.0.0.1/{i}',
                            'location': '测试区', 'tasks': [], 'regions': []})
            self.assertEqual(len(catalog.Store(Path(folder)).cameras), 4)

    def test_reject_bad_polygon(self):
        self.assertTrue(hasattr(regions, 'validate_polygon'), '需要区域校验')
        for points in [[], [[0, 0], [1, 1]], [[0, 0], [1, 1], [1, 0], [0, 1]],
                       [[0, 0], [.5, .5], [1, 1]], [[0, 0], [2, 0], [1, 1]]]:
            with self.assertRaises(ValueError):
                regions.validate_polygon(points)
        regions.validate_polygon(POLYGON)

    def test_entry_stay_exit_reentry_and_two_regions(self):
        self.assertTrue(hasattr(regions, 'EntryState'), '需要按目标记录进入状态')
        state = regions.EntryState()
        zones = [{'id': 'z1', 'name': '禁区一', 'points': POLYGON},
                 {'id': 'z2', 'name': '禁区二', 'points': POLYGON}]
        inside = [{'trackId': 7, 'bbox': [.3, .3, .5, .6], 'label': 'person'}]
        outside = [{'trackId': 7, 'bbox': [0, 0, .1, .1], 'label': 'person'}]
        self.assertEqual(len(state.update(inside, zones)), 2)
        self.assertEqual(state.update(inside, zones), [])
        state.update(outside, zones)
        self.assertEqual(len(state.update(inside, zones)), 2)

    def test_reject_invalid_task_and_path(self):
        self.assertTrue(hasattr(catalog, 'validate_camera'), '需要摄像头配置校验')
        base = {'id': 'cam1', 'name': '视频', 'url': 'rtsp://localhost/a',
                'tasks': [], 'regions': []}
        for change in [{'id': '../outside'}, {'url': 'file:///secret'},
                       {'tasks': [{'eventId': 'cloud'}]},
                       {'tasks': [{'eventId': 'person', 'confidence': float('nan')}]}]:
            data = {**copy.deepcopy(base), **change}
            with self.assertRaises(ValueError):
                catalog.validate_camera(data)


if __name__ == '__main__':
    unittest.main()
