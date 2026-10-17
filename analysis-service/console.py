"""本地 YOLO 视频管理控制台。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import copy
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit, unquote
import uuid
from catalog import ROOT, Store, EVENTS, model_status, validate_camera, validate_source, atomic_json, identifier, MEDIA_ROOT, resolve_source
from alerts import public_status

VIDEO_TYPES = {'.mp4': 'video/mp4', '.webm': 'video/webm', '.ogg': 'video/ogg', '.mov': 'video/quicktime'}

# 每路视频一个进程并各自加载权重，并发数直接决定内存与 CPU 占用。
# 超出上限时明确拒绝，而不是静默排队把机器拖垮。
MAX_RUNNING = int(os.environ.get('KUANGSHAN_MAX_RUNNING', '6'))

# 取流或推理永久卡住时，心跳文件停止更新；超过该秒数则重建该路进程。
WATCHDOG_SECONDS = int(os.environ.get('KUANGSHAN_WATCHDOG_SECONDS', '30'))


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def preview_for(camera):
    path = resolve_source(camera['url'])
    if isinstance(path, Path) and path.is_file() and path.suffix.lower() in VIDEO_TYPES:
        return {'kind': 'video', 'url': f"/preview/{camera['id']}"}
    return {'kind': 'frames'}


class Controller:
    def __init__(self, work):
        self.work = Path(work).resolve()
        self.store = Store(self.work)
        self.lock = threading.RLock()
        self.jobs = {}
        self.probes = {}
        for name in ('covers', 'runtime', 'evidence', 'outbox'):
            (self.work/name).mkdir(exist_ok=True)
        self.database = self.work/'events.db'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, camera TEXT, at TEXT, record TEXT)')
        self.restarts = {}
        self.watchdog_stop = threading.Event()
        self.watchdog = threading.Thread(target=self._watch, daemon=True)
        self.watchdog.start()

    # ==================== 看门狗 ====================

    def _status_stale(self, cid):
        """心跳文件是否已停止更新（取流或推理可能卡死）。"""
        run = self.run_dir(cid)
        if not run:
            return False
        status = read_json(run/cid/'status.json', {})
        at = status.get('at')
        if not at:
            return False
        try:
            stamp = datetime.fromisoformat(at).timestamp()
        except (TypeError, ValueError):
            return False
        return time.time() - stamp > WATCHDOG_SECONDS

    def _watch(self):
        """定期检查运行中任务的心跳，卡死则重建该路；其他路不受影响。"""
        while not self.watchdog_stop.wait(5):
            try:
                for cid in list(self.jobs):
                    if not self.running(cid) or not self._status_stale(cid):
                        continue
                    with self.lock:
                        if not self.running(cid) or not self._status_stale(cid):
                            continue
                        self.restarts[cid] = self.restarts.get(cid, 0) + 1
                        # 只操作本服务拥有的进程；重建期间该路短暂中断，
                        # 同路模型会一起重启，这是首版明确的隔离边界。
                        self._terminate(self.jobs[cid]['process'])
                        self.jobs.pop(cid, None)
                        try:
                            self.start(cid)
                        except (ValueError, OSError):
                            pass
            except Exception:
                # 看门狗自身不得因单次异常而退出。
                continue

    def _terminate(self, process):
        """先请求退出，超时后强制结束，避免留下孤儿进程。"""
        if process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=6)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass

    def probe(self, url):
        url = validate_source(url)
        pid = uuid.uuid4().hex
        image = self.work/'covers'/(pid+'.jpg')
        config = self.work/'covers'/(pid+'.json')
        atomic_json(config, {'url': str(resolve_source(url)), 'target': str(image)})
        try:
            result = subprocess.run([sys.executable, str(ROOT/'capture.py'), str(config)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12,
                                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
            if result.returncode or not image.exists():
                raise ValueError('无法读取视频帧，请检查地址、账号、网络和视频编码')
        except subprocess.TimeoutExpired:
            raise ValueError('视频连接超时（12 秒），请检查地址或网络') from None
        finally:
            config.unlink(missing_ok=True)
        with self.lock:
            self.probes = {k: v for k, v in self.probes.items() if time.monotonic()-v[1] < 3600}
            self.probes[pid] = (url, time.monotonic())
        return {'coverToken': pid, 'cover': '/covers/'+pid+'.jpg'}

    def save(self, data):
        if not isinstance(data, dict):
            raise ValueError('视频配置格式错误')
        data = copy.deepcopy(data)
        data['id'] = data.get('id') or uuid.uuid4().hex[:12]
        data = validate_camera(data)
        old = self.store.get(data['id'])
        if self.running(data['id']):
            raise ValueError('请先停止这路分析再修改配置')
        token = data.pop('coverToken', '')
        if token in self.probes and self.probes[token][0] == data['url']:
            data['cover'] = '/covers/'+token+'.jpg'
        elif old and old['url'] == data['url'] and old.get('cover'):
            data['cover'] = old['cover']
        else:
            raise ValueError('请先测试连接并生成封面，再保存视频')
        return self.store.save(data)

    def running(self, cid):
        job = self.jobs.get(cid)
        return bool(job and job['process'].poll() is None)

    def running_count(self):
        return sum(1 for cid in list(self.jobs) if self.running(cid))

    def start(self, cid):
        camera = self.store.get(cid)
        if not camera:
            raise ValueError('视频不存在')
        if self.running(cid):
            return
        # 容量上限：每路独占进程并各自加载权重，超过上限直接拒绝而非静默积压。
        if self.running_count() >= MAX_RUNNING:
            raise ValueError(f'同时运行的视频已达上限 {MAX_RUNNING} 路，请先停止其他视频')
        camera = validate_camera(camera)
        tasks = [t for t in camera['tasks'] if t['enabled']]
        ready = {m['id'] for m in model_status() if m['ready']}
        if not tasks:
            raise ValueError('请先为视频绑定检测场景')
        if any(t['model'] not in ready for t in tasks):
            raise ValueError('所选场景的模型权重尚未验证')
        camera['eventDb'] = str(self.database)
        camera['cameraName'] = camera['name']
        # outbox 按摄像头固定在 work/outbox/<id>，与每次运行的一次性目录解耦，
        # 使停止/重启后未发送的告警仍能被找回并补发。
        camera['outbox'] = str(self.work/'outbox'/cid/'outbox.db')
        folder = self.work/'runtime'/cid/uuid.uuid4().hex
        folder.mkdir(parents=True)
        config = folder/'config.json'
        atomic_json(config, {'cameras': [camera]})
        log = (folder/'service.log').open('wb')
        try:
            process = subprocess.Popen([sys.executable, str(ROOT/'service.py'), '--config', str(config),
                                        '--output', str(folder), '--duration', '0'],
                                       stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0)
        finally:
            log.close()
        self.jobs[cid] = {'process': process, 'folder': folder}

    def run_dir(self, cid):
        job = self.jobs.get(cid)
        if not job:
            return None
        return next((p for p in job['folder'].iterdir() if p.is_dir()), None)

    def stop(self, cid):
        job = self.jobs.get(cid)
        if not job or job['process'].poll() is not None:
            return
        deadline = time.monotonic()+6
        run = self.run_dir(cid)
        while run is None and time.monotonic() < deadline:
            time.sleep(.1)
            run = self.run_dir(cid)
        if run:
            (run/'STOP').touch()
        try:
            job['process'].wait(timeout=18)
        except subprocess.TimeoutExpired:
            # 优雅停止超时：强制结束，避免关闭时留下孤儿取流进程。
            self._terminate(job['process'])
            raise ValueError('停止超时，已强制结束该路进程，请查看服务日志') from None

    def delete(self, cid):
        if not self.store.get(cid):
            raise ValueError('视频不存在')
        self.stop(cid)
        self.store.delete(cid)
        self.jobs.pop(cid, None)

    def state(self):
        cameras = []
        for camera in self.store.cameras:
            cid = camera['id']
            running = self.running(cid)
            run = self.run_dir(cid)
            status = read_json(run/cid/'status.json', {}) if run else {}
            state = status.get('state', 'starting') if running else 'stopped'
            job = self.jobs.get(cid)
            if job and job['process'].poll() not in (None, 0):
                state = 'error'
            latest = copy.deepcopy(status.get('latest', {}))
            # 叠加层跟随当前配置：删除/解绑区域后，不再展示旧分析快照里的区域框。
            zones = {z['id']: z for z in camera.get('regions', [])}
            tasks = {t['eventId']: t for t in camera.get('tasks', [])}
            live = ''
            if run and status.get('live'):
                live_path = (run / status['live']).resolve()
                if live_path.is_file() and live_path.is_relative_to((self.work/'runtime').resolve()):
                    stamp = int(live_path.stat().st_mtime_ns // 1_000_000)
                    live = '/media/' + live_path.relative_to(self.work/'runtime').as_posix() + f'?v={stamp}'
            for record in latest.values():
                # 分析中优先用同步 live 帧，检测框与画面内容一致。
                record['image'] = live or ('/media/' + (run/record['snapshot']).relative_to(self.work/'runtime').as_posix())
                task = tasks.get(record.get('eventId'))
                bound = set(task.get('regionIds', [])) if task else set()
                record['regions'] = [copy.deepcopy(zones[zid]) for zid in bound
                                    if zid in zones and zones[zid].get('enabled', True)]
            cameras.append({**camera, 'running': running, 'state': state, 'latest': latest, 'liveImage': live,
                            'modelErrors': status.get('modelErrors', {}), 'results': status.get('results', 0),
                            'restarts': self.restarts.get(cid, 0),
                            'alerts': status.get('alerts', {}),
                            'resource': status.get('resource', {}),
                            'preview': preview_for(camera)})
        with closing(sqlite3.connect(self.database)) as db, db:
            count = db.execute('SELECT COUNT(*) FROM alerts').fetchone()[0]
            alerts = [json.loads(x[0]) for x in db.execute('SELECT record FROM alerts ORDER BY at DESC LIMIT 100')]
        # 汇总各路上报状态，便于在界面上直接看出告警是否真的发出去了。
        bridge = public_status()
        detail = {'reported': 0, 'failed': 0, 'pending': 0}
        for camera in cameras:
            info = camera.get('alerts') or {}
            for key in detail:
                detail[key] += int(info.get(key) or 0)
        bridge['detail'] = detail
        # 汇总运行中任务的资源占用，为并发容量评估提供实测依据。
        used = {'rssMb': 0.0, 'cpuPercent': 0.0}
        for camera in cameras:
            if not camera.get('running'):
                continue
            info = camera.get('resource') or {}
            used['rssMb'] += float(info.get('rssMb') or 0)
            used['cpuPercent'] += float(info.get('cpuPercent') or 0)
        return {'cameras': cameras, 'models': model_status(), 'events': EVENTS,
                'alerts': alerts, 'alertCount': count, 'alertBridge': bridge,
                'capacity': {'maxRunning': MAX_RUNNING, 'running': self.running_count(),
                             'watchdogSeconds': WATCHDOG_SECONDS,
                             'rssMb': round(used['rssMb'], 1),
                             'cpuPercent': round(used['cpuPercent'], 1)}}

    def alerts_csv(self):
        """把事件记录导出为 CSV 文本（字段与页面事件表一致）。"""
        import csv
        import io
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(['报警编号', '事件类型', '来源视频', '布控区域', '风险', '置信度', '发生时间', '证据图'])
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute('SELECT record FROM alerts ORDER BY at DESC').fetchall()
        for (raw,) in rows:
            try:
                record = json.loads(raw)
            except ValueError:
                continue
            detections = record.get('detections') or []
            zones = [d.get('regionName') for d in detections if d.get('regionName')]
            confidence = max((d.get('confidence') or 0) for d in detections) if detections else 0
            writer.writerow([
                record.get('alertCode') or record.get('id', ''),
                record.get('eventName') or record.get('eventId', ''),
                record.get('cameraName', ''),
                '、'.join(dict.fromkeys(zones)) or '全画面',
                '低' if record.get('engine') in ('local', 'opencv') else '中',
                f'{confidence * 100:.2f}',
                record.get('capturedAt', ''),
                record.get('image', ''),
            ])
        return buffer.getvalue()

    def preview_path(self, cid):
        camera = self.store.get(cid)
        if not camera:
            return None
        path = resolve_source(camera['url'])
        if isinstance(path, Path) and path.is_file() and path.suffix.lower() in VIDEO_TYPES:
            return path.resolve()
        return None

    def samples(self):
        media = MEDIA_ROOT/'mediamtx/videos'
        # 使用正斜杠，避免 Windows 反斜杠破坏前端 option value。
        return [{'name': p.stem, 'url': p.resolve().as_posix()} for p in sorted(media.glob('*.mp4'))]

    def close(self):
        """关闭控制台：停止看门狗并结束本服务拥有的所有任务。

        关闭路径不能因单路停止失败而中断，否则会留下仍在取流的孤儿进程。
        """
        self.watchdog_stop.set()
        for cid in list(self.jobs):
            try:
                self.stop(cid)
            except (ValueError, OSError):
                self._terminate(self.jobs[cid]['process'])
            finally:
                self.jobs.pop(cid, None)


def make_handler(controller, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def reply(self, status, data, kind='application/json; charset=utf-8'):
            raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(raw)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

        def reply_video(self, path):
            kind = VIDEO_TYPES[path.suffix.lower()]
            size = path.stat().st_size
            start, end = 0, size - 1
            status = 200
            header = self.headers.get('Range')
            if header and header.startswith('bytes='):
                match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
                if not match:
                    return self.reply(416, {'error': '无效的 Range 请求'})
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = int(match.group(2))
                elif match.group(1):
                    end = min(start + 1024 * 1024 - 1, size - 1)
                if start >= size or end >= size or start > end:
                    self.send_response(416)
                    self.send_header('Content-Range', f'bytes */{size}')
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    return
                status = 206
            length = end - start + 1
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Accept-Ranges', 'bytes')
            self.send_header('Content-Length', str(length))
            if status == 206:
                self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            try:
                with path.open('rb') as handle:
                    handle.seek(start)
                    remaining = length
                    while remaining:
                        chunk = handle.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError):
                pass

        def host_ok(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')

        def do_GET(self):
            if not self.host_ok():
                return self.reply(403, {'error': '仅允许本机访问'})
            route = unquote(urlsplit(self.path).path)
            static = {'/': ('console.html', 'text/html; charset=utf-8'),
                      '/console.css': ('console.css', 'text/css; charset=utf-8'),
                      '/console.js': ('console.js', 'text/javascript; charset=utf-8')}
            if route in static:
                name, kind = static[route]
                return self.reply(200, (ROOT/name).read_bytes(), kind)
            if route == '/api/state':
                with controller.lock:
                    data = controller.state()
                return self.reply(200, {**data, 'token': token})
            if route == '/api/samples':
                return self.reply(200, controller.samples())
            if route == '/api/alerts/export.csv':
                # 事件记录导出：带 BOM，保证 Excel 直接打开中文不乱码。
                body = '\ufeff' + controller.alerts_csv()
                raw = body.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/csv; charset=utf-8')
                self.send_header('Content-Disposition', 'attachment; filename="alerts.csv"')
                self.send_header('Content-Length', str(len(raw)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            if route.startswith('/preview/'):
                cid = route[len('/preview/'):]
                if not identifier(cid):
                    return self.reply(404, {'error': '资源不存在'})
                path = controller.preview_path(cid)
                if not path:
                    return self.reply(404, {'error': '该视频不支持连续预览'})
                return self.reply_video(path)
            for prefix, folder in [('/covers/', 'covers'), ('/media/', 'runtime'), ('/evidence/', 'evidence')]:
                if route.startswith(prefix):
                    base = (controller.work/folder).resolve()
                    file = (base/route[len(prefix):]).resolve()
                    if file.is_relative_to(base) and file.suffix.lower() in ('.jpg', '.jpeg', '.png') and file.is_file():
                        try:
                            return self.reply(200, file.read_bytes(), 'image/png' if file.suffix.lower() == '.png' else 'image/jpeg')
                        except OSError:
                            break
            self.reply(404, {'error': '资源不存在'})

        def do_POST(self):
            if not self.host_ok() or self.headers.get('Origin') not in (None, 'http://'+self.headers.get('Host', '')) or not secrets.compare_digest(self.headers.get('X-Console-Token', ''), token):
                return self.reply(403, {'error': '请求校验失败，请刷新页面'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size <= 262144 or self.headers.get_content_type() != 'application/json':
                    raise ValueError('请求格式错误')
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('请求格式错误')
                if self.path == '/api/cameras/probe':
                    return self.reply(200, controller.probe(data.get('url')))
                with controller.lock:
                    if self.path == '/api/cameras/save':
                        return self.reply(200, {'camera': controller.save(data)})
                    actions = {'/api/cameras/start': controller.start, '/api/cameras/stop': controller.stop,
                               '/api/cameras/delete': controller.delete}
                    if self.path not in actions or not identifier(data.get('id')):
                        raise ValueError('操作或视频编号无效')
                    actions[self.path](data['id'])
                self.reply(200, {'ok': True})
            except (ValueError, TypeError) as exc:
                message = str(exc)
                self.reply(400, {'error': message if any('一' <= c <= '鿿' for c in message) else '参数格式错误'})
            except (OSError, sqlite3.Error):
                self.reply(500, {'error': '文件或数据库操作失败，请检查目录权限'})
    return Handler


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--data', type=Path, default=ROOT/'data')
    args = parser.parse_args()
    controller = Controller(args.data)
    server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(controller, secrets.token_hex(24)))
    print(f'http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        controller.close()
