"""告警积压清理：批量处置早于保留窗口的未处置记录。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from alert_disposition import apply_disposition, disposition_of


def _parse_at(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def bulk_dispose_pending(
    database: Path | str,
    *,
    keep_within_hours: float = 24,
    disposition: str = 'handled',
    handled_by: str = '系统清理',
    note: str = '历史未处置积压清理',
    now: datetime | None = None,
) -> dict:
    """将早于保留窗口的 pending 告警批量处置；近 keep_within_hours 内保留。"""
    if disposition not in ('handled', 'false_positive'):
        raise ValueError('处置结果须为已处置或误报')
    hours = float(keep_within_hours)
    if hours < 0 or hours > 24 * 365:
        raise ValueError('保留窗口无效')
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = moment - timedelta(hours=hours)
    updated = 0
    skipped_recent = 0
    skipped_other = 0
    failed = 0
    with closing(sqlite3.connect(Path(database), timeout=60)) as db, db:
        rows = db.execute('SELECT id, at, record FROM alerts').fetchall()
        for alert_id, at, raw in rows:
            try:
                record = json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                failed += 1
                continue
            if not isinstance(record, dict) or disposition_of(record) != 'pending':
                skipped_other += 1
                continue
            when = _parse_at(at) or _parse_at(record.get('capturedAt'))
            if when is None:
                failed += 1
                continue
            if when >= cutoff:
                skipped_recent += 1
                continue
            try:
                new_record = apply_disposition(
                    record, disposition, handled_by=handled_by, note=note, at=moment)
            except ValueError:
                failed += 1
                continue
            db.execute(
                'UPDATE alerts SET record=? WHERE id=?',
                (json.dumps(new_record, ensure_ascii=False), alert_id))
            updated += 1
    return {
        'ok': True,
        'updated': updated,
        'keptRecent': skipped_recent,
        'skippedOther': skipped_other,
        'failed': failed,
        'keepWithinHours': hours,
        'cutoff': cutoff.isoformat(),
        'disposition': disposition,
    }
