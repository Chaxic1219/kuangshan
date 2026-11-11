"""告警处置状态：规范化、风险级别、写回 record。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from catalog import EVENTS

DISPOSITIONS = ('pending', 'handled', 'false_positive')
RISK_LEVELS = ('high', 'medium', 'low')
RISK_HIGH = frozenset({
    'intrusion', 'vehicle_intrusion', 'fire', 'smoke', 'no_helmet', 'no_vest',
})
RISK_MEDIUM = frozenset({'brightness'})


def disposition_of(record: dict | None) -> str:
    """旧记录无字段时视为 pending。"""
    if not isinstance(record, dict):
        return 'pending'
    value = record.get('disposition')
    if value in DISPOSITIONS:
        return value
    return 'pending'


def risk_level(event_id: str | None) -> str:
    if event_id in RISK_HIGH:
        return 'high'
    if event_id in RISK_MEDIUM:
        return 'medium'
    if event_id in EVENTS and EVENTS[event_id].get('alarm'):
        return 'medium'
    return 'low'


def risk_rank(level: str) -> int:
    return {'high': 0, 'medium': 1, 'low': 2}.get(level, 3)


def apply_disposition(record: dict, disposition: str, *, handled_by: str = '',
                      note: str = '', at: datetime | None = None) -> dict:
    """返回更新后的 record 副本；非法 disposition 抛 ValueError。"""
    if disposition not in ('handled', 'false_positive'):
        raise ValueError('处置结果须为已处置或误报')
    if not isinstance(record, dict):
        raise ValueError('告警记录无效')
    current = disposition_of(record)
    if current != 'pending':
        raise ValueError('该告警已处置，不能重复操作')
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    updated = dict(record)
    updated['disposition'] = disposition
    updated['handledAt'] = moment.astimezone(timezone.utc).isoformat()
    updated['handledBy'] = (handled_by or '').strip()[:64]
    text = (note or '').strip()
    if text:
        updated['dispositionNote'] = text[:200]
    else:
        updated.pop('dispositionNote', None)
    return updated


def public_fields(record: dict | None, event_id: str | None = None) -> dict[str, Any]:
    eid = event_id or (record or {}).get('eventId')
    return {
        'disposition': disposition_of(record),
        'riskLevel': risk_level(eid if isinstance(eid, str) else None),
        'handledAt': (record or {}).get('handledAt') or '',
        'handledBy': (record or {}).get('handledBy') or '',
        'dispositionNote': (record or {}).get('dispositionNote') or '',
    }
