"""紧急联系人：本机 JSON 持久化，供值班总览快速通知。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

MAX_CONTACTS = 20


def _normalize_item(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError('联系人格式无效')
    name = str(raw.get('name') or '').strip()
    org = str(raw.get('org') or '').strip()
    phone = str(raw.get('phone') or '').strip()
    note = str(raw.get('note') or '').strip()
    if not name:
        raise ValueError('联系人姓名不能为空')
    if len(name) > 64 or len(org) > 64 or len(phone) > 32 or len(note) > 120:
        raise ValueError('联系人字段过长')
    cid = str(raw.get('id') or '').strip() or uuid.uuid4().hex
    if len(cid) > 64:
        raise ValueError('联系人编号无效')
    return {'id': cid, 'name': name, 'org': org, 'phone': phone, 'note': note}


def load_contacts(path: Path | str) -> dict:
    file = Path(path)
    if not file.is_file():
        return {'contacts': []}
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {'contacts': []}
    rows = data.get('contacts') if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {'contacts': []}
    out = []
    for item in rows[:MAX_CONTACTS]:
        try:
            out.append(_normalize_item(item))
        except ValueError:
            continue
    return {'contacts': out}


def save_contacts(path: Path | str, payload: Any) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('请求格式错误')
    rows = payload.get('contacts')
    if not isinstance(rows, list):
        raise ValueError('联系人列表无效')
    if len(rows) > MAX_CONTACTS:
        raise ValueError(f'联系人最多 {MAX_CONTACTS} 组')
    normalized = [_normalize_item(item) for item in rows]
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(file.suffix + '.tmp')
    body = json.dumps({'contacts': normalized}, ensure_ascii=False, indent=2)
    tmp.write_text(body + '\n', encoding='utf-8')
    tmp.replace(file)
    return {'contacts': normalized}
