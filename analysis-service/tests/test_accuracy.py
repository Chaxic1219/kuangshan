"""准确率测量工具：IoU 匹配、指标计算、标注校验与“不判定合格”边界。"""
import json
import tempfile
import unittest
from pathlib import Path

import evaluate_accuracy as accuracy


def box(label, bbox, confidence=0.9):
    return {'label': label, 'bbox': bbox, 'confidence': confidence}


class MetricTests(unittest.TestCase):
    def test_perfect_match(self):
        labels = accuracy.compute_metrics(
            [[{'label': 'person', 'bbox': [0.1, 0.1, 0.5, 0.5]}]],
            [[box('person', [0.1, 0.1, 0.5, 0.5])]])
        item = labels['person']
        self.assertEqual((item['tp'], item['fp'], item['fn']), (1, 0, 0))
        self.assertEqual(item['precision'], 1.0)
        self.assertEqual(item['recall'], 1.0)
        self.assertEqual(item['f1'], 1.0)

    def test_missed_detection(self):
        labels = accuracy.compute_metrics(
            [[{'label': 'person', 'bbox': [0.1, 0.1, 0.5, 0.5]}]], [[]])
        self.assertEqual(labels['person']['fn'], 1)
        self.assertEqual(labels['person']['recall'], 0.0)

    def test_false_positive(self):
        labels = accuracy.compute_metrics(
            [[]], [[box('person', [0.1, 0.1, 0.5, 0.5])]])
        self.assertEqual(labels['person']['fp'], 1)
        self.assertEqual(labels['person']['precision'], 0.0)

    def test_iou_threshold_boundary(self):
        truth = [{'label': 'person', 'bbox': [0.0, 0.0, 0.5, 1.0]}]
        pred = [box('person', [0.0, 0.0, 0.25, 1.0])]  # IoU = 0.5
        labels = accuracy.compute_metrics([truth], [pred], iou_threshold=0.5)
        self.assertEqual(labels['person']['tp'], 1)
        labels = accuracy.compute_metrics([truth], [pred], iou_threshold=0.51)
        self.assertEqual(labels['person']['tp'], 0)
        self.assertEqual(labels['person']['fp'], 1)
        self.assertEqual(labels['person']['fn'], 1)

    def test_different_labels_do_not_match(self):
        labels = accuracy.compute_metrics(
            [[{'label': 'person', 'bbox': [0.1, 0.1, 0.5, 0.5]}]],
            [[box('NO-Hardhat', [0.1, 0.1, 0.5, 0.5])]])
        self.assertEqual(labels['person']['fn'], 1)
        self.assertEqual(labels['NO-Hardhat']['fp'], 1)

    def test_duplicate_predictions_are_one_to_one(self):
        labels = accuracy.compute_metrics(
            [[{'label': 'person', 'bbox': [0.1, 0.1, 0.5, 0.5]}]],
            [[box('person', [0.1, 0.1, 0.5, 0.5], 0.9),
              box('person', [0.1, 0.1, 0.5, 0.5], 0.8)]])
        self.assertEqual(labels['person']['tp'], 1)
        self.assertEqual(labels['person']['fp'], 1)
        self.assertEqual(labels['person']['fn'], 0)

    def test_iou_values(self):
        self.assertEqual(accuracy.iou([0, 0, 1, 1], [0, 0, 1, 1]), 1.0)
        self.assertEqual(accuracy.iou([0, 0, 0.5, 1], [0.5, 0, 1, 1]), 0.0)
        self.assertAlmostEqual(accuracy.iou([0, 0, 0.5, 1], [0, 0, 0.5, 0.5]), 0.5)

    def test_overall_aggregation(self):
        labels = {
            'person': {'tp': 2, 'fp': 1, 'fn': 1},
            'fire': {'tp': 1, 'fp': 0, 'fn': 0},
        }
        overall = accuracy.overall_from_labels(labels)
        self.assertEqual((overall['tp'], overall['fp'], overall['fn']), (3, 1, 1))
        self.assertAlmostEqual(overall['precision'], 0.75)
        self.assertAlmostEqual(overall['recall'], 0.75)


class AnnotationTests(unittest.TestCase):
    def test_load_valid_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'labels.jsonl'
            path.write_text(json.dumps({
                'image': 'a.jpg',
                'boxes': [{'label': 'person', 'bbox': [0.1, 0.1, 0.5, 0.5]}]}) + '\n',
                encoding='utf-8')
            data = accuracy.load_annotations(path)
            self.assertEqual(data['a.jpg'][0]['label'], 'person')

    def test_reject_invalid_annotations(self):
        cases = [
            '{"image": "a.jpg", "boxes": [{"label": "person", "bbox": [0, 0, 2, 1]}]}',
            '{"image": "a.jpg", "boxes": [{"label": "person", "bbox": [0.5, 0.5, 0.1, 1]}]}',
            '{"image": "a.jpg", "boxes": [{"bbox": [0, 0, 1, 1]}]}',
            '{"boxes": []}',
            'not json',
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'labels.jsonl'
            for text in cases:
                path.write_text(text + '\n', encoding='utf-8')
                with self.assertRaises(ValueError):
                    accuracy.load_annotations(path)

    def test_report_never_claims_pass(self):
        source = Path(accuracy.__file__).read_text(encoding='utf-8')
        self.assertNotIn('合格', source, '报告文案不得写“准确率合格”')
        self.assertIn('measurement_only', source)


if __name__ == '__main__':
    unittest.main()
