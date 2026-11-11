"""本机账号：口令策略、登录、用户管理、强制改密。"""
from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path

import auth_users


class AuthUsersTests(unittest.TestCase):
    def test_password_policy(self):
        with self.assertRaises(ValueError):
            auth_users.validate_password_policy('short1')
        with self.assertRaises(ValueError):
            auth_users.validate_password_policy('onlyletters')
        with self.assertRaises(ValueError):
            auth_users.validate_password_policy('12345678')
        auth_users.validate_password_policy('Admin123')

    def test_admin_not_forced_and_operator_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'users.json'
            auth_users.ensure_users(path)

            with self.assertRaises(ValueError):
                auth_users.login(path, 'admin', 'wrong')

            ok = auth_users.login(path, 'admin', auth_users.DEFAULT_PASSWORD)
            self.assertTrue(ok['success'])
            self.assertTrue(ok['data']['mustChangePassword'])
            token = ok['data']['accessToken']
            self.assertEqual(auth_users.session_user(token), 'admin')
            self.assertEqual(auth_users.require_admin(path, token), 'admin')

            auth_users.change_password(
                path, 'admin', auth_users.DEFAULT_PASSWORD, 'AdminSafe9')
            admin_ok = auth_users.login(path, 'admin', 'AdminSafe9')
            self.assertFalse(admin_ok['data']['mustChangePassword'])

            created = auth_users.create_user(
                path, 'operator1', 'TempPass1', nickname='值班', roles=['operator'])
            self.assertTrue(created['user']['mustChangePassword'])

            op = auth_users.login(path, 'operator1', 'TempPass1')
            self.assertTrue(op['data']['mustChangePassword'])
            with self.assertRaises(PermissionError):
                auth_users.require_admin(path, op['data']['accessToken'])

            auth_users.change_password(path, 'operator1', 'TempPass1', 'SafePass88')
            again = auth_users.login(path, 'operator1', 'SafePass88')
            self.assertFalse(again['data']['mustChangePassword'])

            reset = auth_users.reset_password(path, 'operator1', 'TempPass2')
            self.assertTrue(reset['mustChangePassword'])
            forced = auth_users.login(path, 'operator1', 'TempPass2')
            self.assertTrue(forced['data']['mustChangePassword'])

            admin_reset = auth_users.reset_password(
                path, 'admin', 'Admin999x')
            self.assertTrue(admin_reset['mustChangePassword'])
            admin_again = auth_users.login(path, 'admin', 'Admin999x')
            self.assertTrue(admin_again['data']['mustChangePassword'])
            auth_users.change_password(path, 'admin', 'Admin999x', 'AdminSafe9')

            with self.assertRaises(ValueError):
                auth_users.delete_user(path, 'admin', actor='admin')
            auth_users.delete_user(path, 'operator1', actor='admin')
            listed = auth_users.list_users(path)
            self.assertEqual([u['username'] for u in listed['users']], ['admin'])

            # 迁移：仍为出厂默认口令时必须强制改密
            bad = {
                'users': [{
                    'username': 'admin',
                    'nickname': '管理员',
                    'password': auth_users._hash_password('Admin123'),
                    'mustChangePassword': False,
                    'roles': ['admin'],
                    'permissions': ['*:*:*'],
                }]
            }
            mig = Path(tmp) / 'migrate.json'
            import json
            mig.write_text(json.dumps(bad), encoding='utf-8')
            data = auth_users.ensure_users(mig)
            self.assertTrue(data['users'][0]['mustChangePassword'])

            importlib.reload(auth_users)

    def test_sessions_survive_reload(self):
        """会话落盘后，模拟进程重启仍可识别 Bearer。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'users.json'
            auth_users.bind_session_file(path)
            auth_users.ensure_users(path)
            ok = auth_users.login(path, 'admin', auth_users.DEFAULT_PASSWORD)
            token = ok['data']['accessToken']
            self.assertEqual(auth_users.session_user(token), 'admin')
            # 模拟重启：清空内存后应能从 sessions.json 自动恢复
            auth_users._sessions.clear()
            self.assertEqual(auth_users.session_user(token), 'admin')
            listed = auth_users.require_admin(path, token)
            self.assertEqual(listed, 'admin')

    def test_update_profile_nickname_and_avatar(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'users.json'
            auth_users.ensure_users(path)
            auth_users.login(path, 'admin', auth_users.DEFAULT_PASSWORD)
            updated = auth_users.update_profile(
                path,
                'admin',
                nickname='矿山管理员',
                avatar='data:image/jpeg;base64,abc')
            self.assertTrue(updated['success'])
            self.assertEqual(updated['user']['nickname'], '矿山管理员')
            self.assertTrue(updated['user']['avatar'].startswith('data:image/'))
            again = auth_users.login(path, 'admin', auth_users.DEFAULT_PASSWORD)
            self.assertEqual(again['data']['nickname'], '矿山管理员')
            self.assertTrue(again['data']['avatar'].startswith('data:image/'))
            with self.assertRaises(ValueError):
                auth_users.update_profile(
                    path, 'admin', avatar='http://evil.example/a.png')


if __name__ == '__main__':
    unittest.main()
