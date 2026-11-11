"""测试用：登录后拿 Bearer，供控制台写接口调用。"""
from __future__ import annotations

import auth_users


def admin_bearer(controller, password: str | None = None) -> str:
    result = auth_users.login(
        controller.users_path(),
        auth_users.DEFAULT_USERNAME,
        password or auth_users.DEFAULT_PASSWORD,
    )
    if not result.get('success'):
        raise RuntimeError(result.get('message') or '测试登录失败')
    return result['data']['accessToken']


def auth_headers(bearer: str, *, host: str, content_length: int | None = None) -> dict:
    headers = {
        'Host': host,
        'Authorization': f'Bearer {bearer}',
    }
    if content_length is not None:
        headers['Content-Type'] = 'application/json'
        headers['Content-Length'] = str(content_length)
    return headers
