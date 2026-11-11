"""本机企业账号：口令策略、用户文件、会话、用户管理。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

DEFAULT_USERNAME = 'admin'
DEFAULT_PASSWORD = 'Admin123'
SESSION_HOURS = 12
PBKDF2_ROUNDS = 120_000
ALLOWED_ROLES = frozenset({'admin', 'operator'})
USERNAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_]{2,31}$')

_lock = threading.RLock()
_sessions: dict[str, dict[str, Any]] = {}
_session_file: Path | None = None


def bind_session_file(users_path: Path | str) -> None:
    """会话落盘到 users.json 同目录 sessions.json，避免控制台重启后“仍显示已登录但接口说未登录”。"""
    global _session_file
    with _lock:
        _session_file = Path(users_path).resolve().with_name('sessions.json')
        _load_sessions_unlocked()


def _load_sessions_unlocked() -> None:
    global _sessions
    _sessions = {}
    if not _session_file or not _session_file.is_file():
        return
    try:
        raw = json.loads(_session_file.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return
    rows = raw.get('sessions') if isinstance(raw, dict) else None
    if not isinstance(rows, dict):
        return
    now = time.time()
    for key, row in rows.items():
        if not isinstance(key, str) or not isinstance(row, dict):
            continue
        exp = row.get('expiresAt')
        if not isinstance(exp, (int, float)) or exp < now:
            continue
        _sessions[key] = row


def _persist_sessions_unlocked() -> None:
    if not _session_file:
        return
    now = time.time()
    alive = {key: row for key, row in _sessions.items()
             if isinstance(row, dict) and float(row.get('expiresAt') or 0) >= now}
    _sessions.clear()
    _sessions.update(alive)
    _session_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = _session_file.with_suffix(_session_file.suffix + '.tmp')
    tmp.write_text(
        json.dumps({'sessions': alive}, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8')
    tmp.replace(_session_file)


def validate_password_policy(password: str) -> None:
    if not isinstance(password, str):
        raise ValueError('密码无效')
    if len(password) < 8:
        raise ValueError('密码不少于 8 位')
    if not re.search(r'[A-Za-z]', password) or not re.search(r'\d', password):
        raise ValueError('密码必须同时包含字母和数字')


def _hash_password(password: str, *, salt: bytes | None = None) -> dict:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), salt, PBKDF2_ROUNDS)
    return {
        'algo': 'pbkdf2_sha256',
        'rounds': PBKDF2_ROUNDS,
        'salt': salt.hex(),
        'hash': digest.hex(),
    }


def _verify_password(password: str, record: dict) -> bool:
    try:
        salt = bytes.fromhex(record['salt'])
        expected = bytes.fromhex(record['hash'])
        rounds = int(record.get('rounds') or PBKDF2_ROUNDS)
    except (KeyError, ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac(
        'sha256', password.encode('utf-8'), salt, rounds)
    return hmac.compare_digest(actual, expected)


def _iso_expires(hours: float = SESSION_HOURS) -> str:
    """返回浏览器 Date 可稳定解析的时间（含时区）。"""
    moment = datetime.now(timezone.utc) + timedelta(hours=hours)
    return moment.astimezone().isoformat(timespec='seconds')


def _public_user(user: dict) -> dict:
    return {
        'username': user.get('username') or '',
        'nickname': user.get('nickname') or user.get('username') or '',
        'avatar': user.get('avatar') or '',
        'roles': list(user.get('roles') or []),
        'permissions': list(user.get('permissions') or []),
        'mustChangePassword': bool(user.get('mustChangePassword')),
    }


def _normalize_avatar(avatar: Any) -> str:
    """头像存 data URL 或空串；限制体积避免撑爆 users.json。"""
    if avatar is None:
        return ''
    text = str(avatar).strip()
    if not text:
        return ''
    if not text.startswith('data:image/'):
        raise ValueError('头像须为图片数据')
    if len(text) > 180_000:
        raise ValueError('头像过大，请压缩后再上传')
    return text


def _normalize_roles(roles: Any) -> list[str]:
    if roles is None:
        return ['operator']
    if isinstance(roles, str):
        roles = [roles]
    if not isinstance(roles, list) or not roles:
        raise ValueError('角色无效')
    out: list[str] = []
    for item in roles:
        name = str(item or '').strip()
        if name not in ALLOWED_ROLES:
            raise ValueError('角色仅支持 admin 或 operator')
        if name not in out:
            out.append(name)
    return out


def _validate_username(username: str) -> str:
    name = (username or '').strip()
    if not USERNAME_RE.match(name):
        raise ValueError('用户名须以字母开头，3～32 位字母数字或下划线')
    return name


def _invalidate_user_sessions(username: str) -> None:
    stale = [key for key, row in _sessions.items()
             if row.get('username') == username]
    for key in stale:
        _sessions.pop(key, None)
    _persist_sessions_unlocked()


def _admin_count(data: dict) -> int:
    total = 0
    for user in data.get('users') or []:
        if isinstance(user, dict) and 'admin' in (user.get('roles') or []):
            total += 1
    return total


def _migrate_admin_defaults(file: Path, data: dict) -> dict:
    """纠偏 admin 角色；若仍为出厂默认口令则强制改密。"""
    changed = False
    for user in data.get('users') or []:
        if not isinstance(user, dict):
            continue
        if user.get('username') != DEFAULT_USERNAME:
            continue
        roles = user.get('roles')
        if not isinstance(roles, list) or 'admin' not in roles:
            user['roles'] = ['admin']
            changed = True
        if _verify_password(DEFAULT_PASSWORD, user.get('password') or {}):
            if not user.get('mustChangePassword'):
                user['mustChangePassword'] = True
                changed = True
    if changed:
        _save_users(file, data)
    return data


def ensure_users(path: Path | str) -> dict:
    file = Path(path)
    with _lock:
        if file.is_file():
            try:
                data = json.loads(file.read_text(encoding='utf-8'))
            except (OSError, UnicodeError, json.JSONDecodeError):
                data = {}
            if isinstance(data, dict) and isinstance(data.get('users'), list) and data['users']:
                return _migrate_admin_defaults(file, data)
        payload = {
            'users': [{
                'username': DEFAULT_USERNAME,
                'nickname': '管理员',
                'password': _hash_password(DEFAULT_PASSWORD),
                'mustChangePassword': True,
                'roles': ['admin'],
                'permissions': ['*:*:*'],
            }]
        }
        file.parent.mkdir(parents=True, exist_ok=True)
        _save_users(file, payload)
        return payload


def _save_users(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n',
                   encoding='utf-8')
    tmp.replace(path)


def _find_user(data: dict, username: str) -> dict | None:
    for user in data.get('users') or []:
        if isinstance(user, dict) and user.get('username') == username:
            return user
    return None


def login(path: Path | str, username: str, password: str) -> dict:
    username = (username or '').strip()
    password = password or ''
    if not username or not password:
        raise ValueError('请输入用户名和密码')
    data = ensure_users(path)
    user = _find_user(data, username)
    if not user or not _verify_password(password, user.get('password') or {}):
        raise ValueError('用户名或密码错误')
    token = secrets.token_hex(24)
    refresh = secrets.token_hex(24)
    expires = _iso_expires()
    with _lock:
        _sessions[token] = {
            'username': username,
            'refresh': refresh,
            'expiresAt': time.time() + SESSION_HOURS * 3600,
        }
        _sessions[refresh] = {
            'username': username,
            'accessOf': token,
            'expiresAt': time.time() + SESSION_HOURS * 3600 * 2,
            'kind': 'refresh',
        }
        _persist_sessions_unlocked()
    return {
        'success': True,
        'data': {
            'avatar': user.get('avatar') or '',
            'username': username,
            'nickname': user.get('nickname') or username,
            'roles': list(user.get('roles') or ['operator']),
            'permissions': list(user.get('permissions') or ['*:*:*']),
            'accessToken': token,
            'refreshToken': refresh,
            'expires': expires,
            'mustChangePassword': bool(user.get('mustChangePassword')),
        },
    }


def refresh_session(refresh_token: str) -> dict:
    refresh_token = (refresh_token or '').strip()
    with _lock:
        row = _sessions.get(refresh_token)
        if not row or row.get('kind') != 'refresh' or row['expiresAt'] < time.time():
            raise ValueError('登录已过期，请重新登录')
        username = row['username']
        token = secrets.token_hex(24)
        expires = _iso_expires()
        _sessions[token] = {
            'username': username,
            'refresh': refresh_token,
            'expiresAt': time.time() + SESSION_HOURS * 3600,
        }
        row['expiresAt'] = time.time() + SESSION_HOURS * 3600 * 2
        _persist_sessions_unlocked()
    return {
        'success': True,
        'data': {
            'accessToken': token,
            'refreshToken': refresh_token,
            'expires': expires,
        },
    }


def session_user(access_token: str) -> str | None:
    access_token = (access_token or '').strip()
    with _lock:
        if not _sessions and _session_file:
            _load_sessions_unlocked()
        row = _sessions.get(access_token)
        if not row or row.get('kind') == 'refresh' or row['expiresAt'] < time.time():
            return None
        return row.get('username')


def user_has_admin(path: Path | str, username: str) -> bool:
    if not username:
        return False
    data = ensure_users(path)
    user = _find_user(data, username)
    return bool(user and 'admin' in (user.get('roles') or []))


def require_admin(path: Path | str, access_token: str) -> str:
    username = session_user(access_token)
    if not username:
        raise PermissionError('请先登录')
    if not user_has_admin(path, username):
        raise PermissionError('需要管理员权限')
    return username


def change_password(path: Path | str, username: str, old_password: str,
                    new_password: str) -> dict:
    """凭旧密码改密；不强制依赖浏览器会话。"""
    username = (username or '').strip()
    if not username:
        raise ValueError('用户名无效')
    validate_password_policy(new_password)
    if old_password == new_password:
        raise ValueError('新密码不能与旧密码相同')
    file = Path(path)
    with _lock:
        data = ensure_users(file)
        user = _find_user(data, username)
        if not user or not _verify_password(old_password, user.get('password') or {}):
            raise ValueError('旧密码不正确')
        user['password'] = _hash_password(new_password)
        user['mustChangePassword'] = False
        _save_users(file, data)
        _invalidate_user_sessions(username)
    return {'success': True, 'mustChangePassword': False}


def list_users(path: Path | str) -> dict:
    data = ensure_users(path)
    rows = [_public_user(u) for u in (data.get('users') or []) if isinstance(u, dict)]
    return {'success': True, 'users': rows}


def create_user(path: Path | str, username: str, password: str,
                nickname: str = '', roles: Any = None) -> dict:
    username = _validate_username(username)
    if username == DEFAULT_USERNAME:
        raise ValueError('不能创建名为 admin 的账号')
    validate_password_policy(password)
    nickname = (nickname or '').strip() or username
    if len(nickname) > 64:
        raise ValueError('昵称过长')
    role_list = _normalize_roles(roles if roles is not None else ['operator'])
    file = Path(path)
    with _lock:
        data = ensure_users(file)
        if _find_user(data, username):
            raise ValueError('用户名已存在')
        user = {
            'username': username,
            'nickname': nickname,
            'password': _hash_password(password),
            'mustChangePassword': True,
            'roles': role_list,
            'permissions': ['*:*:*'],
        }
        data.setdefault('users', []).append(user)
        _save_users(file, data)
    return {'success': True, 'user': _public_user(user)}


def update_profile(path: Path | str, username: str, *, nickname: str | None = None,
                   avatar: Any = None) -> dict:
    """登录用户更新自己的昵称与头像（不可改角色）。"""
    username = (username or '').strip()
    if not username:
        raise ValueError('用户名无效')
    if nickname is None and avatar is None:
        raise ValueError('没有可更新的资料')
    file = Path(path)
    with _lock:
        data = ensure_users(file)
        user = _find_user(data, username)
        if not user:
            raise ValueError('用户不存在')
        if nickname is not None:
            nick = nickname.strip() or username
            if len(nick) > 64:
                raise ValueError('昵称过长')
            user['nickname'] = nick
        if avatar is not None:
            user['avatar'] = _normalize_avatar(avatar)
        _save_users(file, data)
    return {'success': True, 'user': _public_user(user)}


def update_user(path: Path | str, username: str, *, nickname: str | None = None,
                roles: Any = None) -> dict:
    username = (username or '').strip()
    if not username:
        raise ValueError('用户名无效')
    file = Path(path)
    with _lock:
        data = ensure_users(file)
        user = _find_user(data, username)
        if not user:
            raise ValueError('用户不存在')
        if nickname is not None:
            nick = nickname.strip() or username
            if len(nick) > 64:
                raise ValueError('昵称过长')
            user['nickname'] = nick
        if roles is not None:
            role_list = _normalize_roles(roles)
            if username == DEFAULT_USERNAME and 'admin' not in role_list:
                raise ValueError('不能移除 admin 账号的管理员角色')
            if ('admin' in (user.get('roles') or [])
                    and 'admin' not in role_list
                    and _admin_count(data) <= 1):
                raise ValueError('至少保留一名管理员')
            user['roles'] = role_list
        _save_users(file, data)
    return {'success': True, 'user': _public_user(user)}


def reset_password(path: Path | str, username: str, new_password: str) -> dict:
    username = (username or '').strip()
    if not username:
        raise ValueError('用户名无效')
    validate_password_policy(new_password)
    file = Path(path)
    with _lock:
        data = ensure_users(file)
        user = _find_user(data, username)
        if not user:
            raise ValueError('用户不存在')
        user['password'] = _hash_password(new_password)
        # 管理员重置后一律要求当事人自行再改一次，避免口令旁路长期留存。
        user['mustChangePassword'] = True
        _save_users(file, data)
        _invalidate_user_sessions(username)
    return {
        'success': True,
        'mustChangePassword': bool(user['mustChangePassword']),
        'user': _public_user(user),
    }


def delete_user(path: Path | str, username: str, *, actor: str = '') -> dict:
    username = (username or '').strip()
    if not username:
        raise ValueError('用户名无效')
    if username == DEFAULT_USERNAME:
        raise ValueError('不能删除 admin 账号')
    if actor and actor == username:
        raise ValueError('不能删除当前登录账号')
    file = Path(path)
    with _lock:
        data = ensure_users(file)
        users = [u for u in (data.get('users') or []) if isinstance(u, dict)]
        target = _find_user({'users': users}, username)
        if not target:
            raise ValueError('用户不存在')
        if 'admin' in (target.get('roles') or []) and _admin_count(data) <= 1:
            raise ValueError('至少保留一名管理员')
        data['users'] = [u for u in users if u.get('username') != username]
        _save_users(file, data)
        _invalidate_user_sessions(username)
    return {'success': True}
