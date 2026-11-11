"""将前端构建目录作为独立入口托管，拒绝越界资源。"""
import mimetypes
from pathlib import Path
import re


def resolve_asset(dist, relative):
    if not relative or any(char in relative for char in ('\\', ':', '\x00')):
        return None
    if relative.startswith('/') or '..' in relative.split('/'):
        return None
    try:
        base = Path(dist).resolve()
        path = (base / relative).resolve()
        return path if path.is_relative_to(base) and path.is_file() else None
    except (OSError, ValueError, RuntimeError):
        return None


def serve_frontend(handler, route, dist):
    """已处理返回 True；旧入口和业务路由交还现有处理器。"""
    if route == '/' and resolve_asset(dist, 'index.html') is None:
        return False
    if route not in ('/', '/ui') and not route.startswith('/ui/'):
        return False
    if route in ('/', '/ui'):
        handler.send_response(302)
        handler.send_header('Location', '/ui/')
        handler.send_header('Content-Length', '0')
        handler.send_header('Cache-Control', 'no-store')
        handler.end_headers()
        return True
    relative = route[len('/ui/'):] or 'index.html'
    path = resolve_asset(dist, relative)
    try:
        raw = path.read_bytes() if path else None
    except OSError:
        raw = None
    if raw is None:
        message = '前端尚未完成构建，请使用 /legacy/ 旧版入口' if relative == 'index.html' else '资源不存在'
        handler.reply(404, {'error': message})
        return True
    suffix = path.suffix.lower()
    kind = {'.js': 'text/javascript', '.mjs': 'text/javascript',
            '.css': 'text/css', '.html': 'text/html', '.woff2': 'font/woff2',
            '.woff': 'font/woff', '.svg': 'image/svg+xml'}.get(suffix)
    kind = kind or mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
    if kind.startswith('text/'):
        kind += '; charset=utf-8'
    hashed = relative.startswith(('assets/', 'static/')) and re.search(r'[-.][A-Za-z0-9_-]{8,}\.[^.]+$', path.name)
    cache = 'public, max-age=31536000, immutable' if hashed and suffix != '.html' else 'no-store'
    handler.send_response(200)
    handler.send_header('Content-Type', kind)
    handler.send_header('Content-Length', str(len(raw)))
    handler.send_header('Cache-Control', cache)
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('X-Frame-Options', 'DENY')
    handler.end_headers()
    try:
        handler.wfile.write(raw)
    except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
        pass
    return True
