import json
import tempfile
from pathlib import Path
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from console import Controller, make_handler
from catalog import MEDIA_ROOT


class ConsoleTests(unittest.TestCase):
    def test_probe_save_reload_and_address_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            c=Controller(Path(folder))
            url=str(MEDIA_ROOT/'mediamtx/videos/camera1.mp4')
            probe=c.probe(url)
            saved=c.save({'id':'','name':'视频一','url':url,'tasks':[],**probe})
            self.assertTrue((Path(folder)/saved['cover'].lstrip('/')).is_file())
            with self.assertRaises(ValueError):
                c.save({'name':'错地址','url':'rtsp://localhost/wrong','coverToken':probe['coverToken']})
            c.close()
            reloaded = Controller(Path(folder))
            try:
                self.assertEqual(len(reloaded.store.cameras),1)
            finally:
                reloaded.close()

    def test_samples_use_posix_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            c = Controller(Path(folder))
            samples = c.samples()
            self.assertGreaterEqual(len(samples), 1)
            for item in samples:
                self.assertNotIn('\\', item['url'])
                self.assertTrue(Path(item['url']).is_file())
            c.close()

    def test_local_video_preview_range(self):
        with tempfile.TemporaryDirectory() as folder:
            c = Controller(Path(folder))
            url = str(MEDIA_ROOT/'mediamtx/videos/camera1.mp4')
            probe = c.probe(url)
            saved = c.save({'id': '', 'name': '预览视频', 'url': url, 'tasks': [], **probe})
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(c, 'test-token'))
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                conn = HTTPConnection('127.0.0.1', server.server_port)
                conn.request('GET', f"/preview/{saved['id']}", headers={'Range': 'bytes=0-2047'})
                response = conn.getresponse()
                body = response.read()
                self.assertEqual(response.status, 206)
                self.assertEqual(response.getheader('Content-Type'), 'video/mp4')
                self.assertEqual(len(body), 2048)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()
                c.close()

    def test_http_guards_and_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            c=Controller(Path(folder))
            server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(c,'test-token'))
            thread=threading.Thread(target=server.serve_forever);thread.start()
            try:
                for method,path,headers,expected in [
                    ('GET','/api/state',{},200),
                    ('GET','/api/state',{'Host':'evil.example'},403),
                    ('GET','/media/../../cameras.json',{},404),
                    ('POST','/api/cameras/start',{},403),
                    ('POST','/api/cameras/start',{'Origin':'https://evil.example','X-Console-Token':'test-token'},403)]:
                    conn=HTTPConnection('127.0.0.1',server.server_port)
                    conn.request(method,path,headers=headers)
                    response=conn.getresponse();response.read()
                    self.assertEqual(response.status,expected)
                    conn.close()
            finally:
                server.shutdown();server.server_close();thread.join();c.close()
