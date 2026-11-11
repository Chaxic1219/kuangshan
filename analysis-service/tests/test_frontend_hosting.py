"""新旧前端入口和构建资源的隔离 HTTP 验证。"""
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import json
import os
import tempfile
import threading
import unittest

import console


class FrontendHostingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.service = self.root / 'analysis-service'
        self.service.mkdir()
        (self.service / 'console.html').write_text('旧控制台', encoding='utf-8')
        (self.service / 'console.js').write_text('旧脚本', encoding='utf-8')
        # 与 console 托管优先级一致：优先 frontend_thin/dist
        self.dist = self.root / 'frontend_thin' / 'dist'
        (self.dist / 'assets').mkdir(parents=True)
        (self.dist / 'index.html').write_text('新控制台', encoding='utf-8')
        (self.dist / 'assets' / 'app-Ab12cd34.js').write_text('export {}', encoding='utf-8')
        (self.dist / 'assets' / 'app-Ab12cd34.css').write_text('body {}', encoding='utf-8')
        (self.dist / 'assets' / 'icon.svg').write_text('<svg/>', encoding='utf-8')
        (self.dist / 'assets' / 'font.woff2').write_bytes(b'font')
        (self.root / 'secret.txt').write_text('机密', encoding='utf-8')
        for folder in ('covers', 'runtime', 'evidence'):
            (self.root / folder).mkdir()
            (self.root / folder / 'test.jpg').write_bytes(b'image')
        self.patch = patch.object(console, 'ROOT', self.service)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        controller = SimpleNamespace(work=self.root, lock=threading.RLock(), state=lambda: {'cameras': []})
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), console.make_handler(controller, 'test-token'))
        self.thread = threading.Thread(target=self.server.serve_forever)
        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, route, headers=None):
        conn = HTTPConnection('127.0.0.1', self.server.server_port)
        try:
            conn.request('GET', route, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_built_entry_and_root_redirect(self):
        status, headers, _ = self.get('/')
        self.assertEqual(status, 302)
        self.assertEqual(headers['Location'], '/ui/')
        for route in ('/ui/', '/ui/index.html?version=1'):
            status, headers, body = self.get(route)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Content-Type'], 'text/html; charset=utf-8')
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertEqual(body.decode(), '新控制台')
        self.assertEqual(self.get('/ui')[1]['Location'], '/ui/')

    def test_missing_build_keeps_legacy_and_explains_missing_ui(self):
        (self.dist / 'index.html').unlink()
        self.assertEqual(self.get('/')[2].decode(), '旧控制台')
        status, _, body = self.get('/ui/')
        self.assertEqual(status, 404)
        self.assertIn('构建', body.decode())

    def test_legacy_and_existing_paths_remain_available(self):
        self.assertEqual(self.get('/legacy/')[2].decode(), '旧控制台')
        self.assertEqual(self.get('/console.js')[2].decode(), '旧脚本')
        self.assertEqual(json.loads(self.get('/api/state')[2])['token'], '')
        for prefix in ('covers', 'media', 'evidence'):
            self.assertEqual(self.get(f'/{prefix}/test.jpg')[2], b'image')
        self.assertEqual(self.get('/ui/', {'Host': 'evil.example'})[0], 403)

    def test_assets_have_correct_mime_and_cache(self):
        for name, mime in [('app-Ab12cd34.js', 'text/javascript; charset=utf-8'),
                           ('app-Ab12cd34.css', 'text/css; charset=utf-8'),
                           ('icon.svg', 'image/svg+xml'), ('font.woff2', 'font/woff2')]:
            status, headers, _ = self.get('/ui/assets/' + name)
            self.assertEqual(status, 200, name)
            self.assertEqual(headers['Content-Type'], mime)
            self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
            expected = 'public, max-age=31536000, immutable' if name.startswith('app-') else 'no-store'
            self.assertEqual(headers['Cache-Control'], expected)

    def test_missing_and_unsafe_resources_return_404(self):
        for route in ('/ui/assets/missing.js', '/ui/cameras', '/ui/assets/',
                      '/ui/../../secret.txt', '/ui/%2e%2e/%2e%2e/secret.txt',
                      '/ui/..%5c..%5csecret.txt', '/ui/C:/secret.txt', '/ui/%00.txt',
                      '/ui//secret.txt', '/ui/assets/font.woff2:stream'):
            self.assertEqual(self.get(route)[0], 404, route)

    def test_template_static_hashes_are_cached_but_plain_files_are_not(self):
        for relative, cached in [('static/js/index-Dab12_34.js', True),
                                 ('static/css/index-Ab12cd34.css', True),
                                 ('static/png/logo-Ab12cd34.png', True),
                                 ('static/js/settings.js', False),
                                 ('platform-config.json', False)]:
            path = self.dist / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'content')
            status, headers, body = self.get('/ui/' + relative)
            self.assertEqual(status, 200)
            self.assertEqual(body, b'content')
            expected = 'public, max-age=31536000, immutable' if cached else 'no-store'
            self.assertEqual(headers['Cache-Control'], expected, relative)

    def test_symlink_cannot_escape_dist(self):
        link = self.dist / 'assets' / 'secret.txt'
        try:
            link.symlink_to(self.root / 'secret.txt')
        except OSError as exc:
            if os.name != 'nt':
                self.skipTest(f'当前系统不允许创建符号链接：{exc}')
            import _winapi
            link = self.dist / 'assets' / 'external'
            _winapi.CreateJunction(str(self.root), str(link))
            route = '/ui/assets/external/secret.txt'
        else:
            route = '/ui/assets/secret.txt'
        self.assertEqual(self.get(route)[0], 404)


if __name__ == '__main__':
    unittest.main()
