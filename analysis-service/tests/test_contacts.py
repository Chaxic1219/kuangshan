"""紧急联系人读写。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import contacts


class ContactsTests(unittest.TestCase):
    def test_roundtrip_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'contacts.json'
            empty = contacts.load_contacts(path)
            self.assertEqual(empty, {'contacts': []})

            saved = contacts.save_contacts(path, {'contacts': [
                {'name': '张三', 'org': '安环部', 'phone': '13800000000', 'note': '白班'},
                {'name': '李四', 'org': '中控', 'phone': '13900000000'},
            ]})
            self.assertEqual(len(saved['contacts']), 2)
            self.assertTrue(saved['contacts'][0]['id'])
            loaded = contacts.load_contacts(path)
            self.assertEqual(loaded['contacts'][0]['name'], '张三')
            self.assertEqual(loaded['contacts'][1]['org'], '中控')

            with self.assertRaises(ValueError):
                contacts.save_contacts(path, {'contacts': [{'name': ''}]})
            with self.assertRaises(ValueError):
                contacts.save_contacts(path, {'contacts': [{'name': 'x'}] * 21})


if __name__ == '__main__':
    unittest.main()
