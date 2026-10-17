import unittest
from catalog import EVENTS, validate_camera


class EventCatalogTests(unittest.TestCase):
    def test_three_weight_groups_and_no_cloud(self):
        self.assertEqual({e['model'] for e in EVENTS.values()}, {'general','ppe','fire','opencv'})
        self.assertEqual(EVENTS['no_helmet']['labels'], ['no-hardhat'])

    def test_same_weight_for_multiple_scenes(self):
        camera = validate_camera({'id':'A','name':'视频','url':'rtsp://localhost/a',
                                  'tasks':[{'eventId':'fire'},{'eventId':'smoke'}]})
        self.assertEqual([t['model'] for t in camera['tasks']], ['fire','fire'])

    def test_intrusion_requires_enabled_zone(self):
        with self.assertRaises(ValueError):
            validate_camera({'id':'A','name':'视频','url':'rtsp://localhost/a','tasks':[{'eventId':'intrusion'}]})
