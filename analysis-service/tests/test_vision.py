"""新版纯本地推理的过滤测试，不调用旧云模型。"""
import unittest
from unittest.mock import Mock
import numpy as np
from catalog import validate_camera
from detectors import SceneAlgo


class LocalVisionTests(unittest.TestCase):
    def test_scene_uses_classes_and_confidence(self):
        task = validate_camera({'id':'a','name':'视频','url':'rtsp://localhost/a',
                                'tasks':[{'eventId':'no_helmet','confidence':.5}]})['tasks'][0]
        pool = Mock()
        pool.detect.return_value = [
            {'label':'NO-Hardhat','confidence':.9,'bbox':[0,0,1,1]},
            {'label':'NO-Hardhat','confidence':.2,'bbox':[0,0,1,1]},
            {'label':'Hardhat','confidence':.9,'bbox':[0,0,1,1]}]
        result=SceneAlgo(task,pool,[]).run([np.zeros((16,16,3),np.uint8)])
        labels={d['label'] for d in result['detections']}
        self.assertEqual(labels,{'NO-Hardhat','Hardhat'})
        alarms=[d for d in result['detections'] if d.get('alarmable')]
        self.assertEqual(len(alarms),1)
        self.assertEqual(alarms[0]['label'],'NO-Hardhat')
