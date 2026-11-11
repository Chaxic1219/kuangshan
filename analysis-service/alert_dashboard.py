"""告警数据大屏：按时间范围从 SQLite 聚合，不依赖 /api/state 最近 100 条。"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
import sqlite3
from pathlib import Path
from typing import Any

from catalog import EVENTS
from alert_disposition import (
    DISPOSITIONS, RISK_LEVELS, disposition_of, public_fields, risk_rank)

# 站点时区固定 UTC+8（与 Asia/Singapore 同偏移）；Windows 无需 tzdata 包。
SITE_TZ = timezone(timedelta(hours=8), name='UTC+8')
MAX_RANGE_DAYS = 90
RECENT_LIMIT = 20
CAMERA_RANK_LIMIT = 10
MAX_PAGE_SIZE = 100

PRESETS = ('today', 'last24h', 'last7d', 'last30d', 'custom')


class DashboardError(ValueError):
    """筛选参数或查询范围无效。"""


def ensure_indexes(database: Path | str) -> None:
    path = Path(database)
    with closing(sqlite3.connect(path, timeout=30)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS alerts (id TEXT PRIMARY KEY, camera TEXT, at TEXT, record TEXT)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_at ON alerts(at)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_alerts_camera_at ON alerts(camera, at)')


def _parse_instant(value: str | None, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise DashboardError(f'请提供有效的{label}')
    text = value.strip().replace('Z', '+00:00')
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DashboardError(f'{label}时间格式无效') from exc
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=SITE_TZ)
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat()


def resolve_window(preset: str = 'today', start: str | None = None, end: str | None = None,
                   now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """返回 UTC 半开区间 [from, to) 与实际 preset。"""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    name = (preset or 'today').strip()
    if name not in PRESETS:
        raise DashboardError('时间范围预设无效')
    if name == 'custom':
        begin = _parse_instant(start, '开始')
        finish = _parse_instant(end, '结束')
    elif name == 'last24h':
        finish = current
        begin = finish - timedelta(hours=24)
    else:
        local_now = current.astimezone(SITE_TZ)
        today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        if name == 'today':
            begin = today
        elif name == 'last7d':
            begin = today - timedelta(days=6)
        else:  # last30d
            begin = today - timedelta(days=29)
        begin = begin.astimezone(timezone.utc)
        finish = current
    if finish <= begin:
        raise DashboardError('结束时间必须晚于开始时间')
    if finish - begin > timedelta(days=MAX_RANGE_DAYS):
        raise DashboardError(f'单次查询最长 {MAX_RANGE_DAYS} 天')
    return begin, finish, name


def _bucket_kind(begin: datetime, finish: datetime) -> str:
    return 'hour' if finish - begin <= timedelta(hours=48) else 'day'


def _iter_buckets(begin: datetime, finish: datetime, kind: str) -> list[str]:
    local_begin = begin.astimezone(SITE_TZ)
    local_end = finish.astimezone(SITE_TZ)
    keys = []
    if kind == 'hour':
        cursor = local_begin.replace(minute=0, second=0, microsecond=0)
        while cursor < local_end:
            keys.append(cursor.strftime('%Y-%m-%dT%H:00'))
            cursor += timedelta(hours=1)
    else:
        cursor = local_begin.replace(hour=0, minute=0, second=0, microsecond=0)
        while cursor < local_end:
            keys.append(cursor.strftime('%Y-%m-%d'))
            cursor += timedelta(days=1)
    return keys


def _bucket_key(at_text: str, kind: str) -> str | None:
    try:
        moment = datetime.fromisoformat(at_text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    local = moment.astimezone(SITE_TZ)
    if kind == 'hour':
        return local.strftime('%Y-%m-%dT%H:00')
    return local.strftime('%Y-%m-%d')


def _parse_moment(text: str | None) -> datetime | None:
    if not text or not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def disposition_metrics(rows: list[dict]) -> dict:
    """本企业周期内考核口径：处置率、平均处置时长（秒）。"""
    total = len(rows)
    disposed = [row for row in rows if row.get('disposition') != 'pending']
    disposed_count = len(disposed)
    pending_count = total - disposed_count
    rate = round(disposed_count / total, 4) if total else None
    durations: list[float] = []
    for row in disposed:
        created = _parse_moment(row.get('at'))
        handled = _parse_moment(row.get('handledAt') or '')
        if not created or not handled:
            continue
        seconds = (handled - created).total_seconds()
        if seconds >= 0:
            durations.append(seconds)
    avg_seconds = round(sum(durations) / len(durations), 1) if durations else None
    return {
        'disposedCount': disposed_count,
        'pendingCount': pending_count,
        'dispositionRate': rate,
        'avgDispositionSeconds': avg_seconds,
    }


def _load_rows(db: sqlite3.Connection, begin: datetime, finish: datetime,
               camera_id: str | None, event_id: str | None,
               disposition: str | None = None) -> tuple[list[dict], int]:
    params: list[Any] = [_iso(begin), _iso(finish)]
    sql = 'SELECT id, camera, at, record FROM alerts WHERE at >= ? AND at < ?'
    if camera_id:
        sql += ' AND camera = ?'
        params.append(camera_id)
    sql += ' ORDER BY at ASC, id ASC'
    excluded = 0
    rows = []
    for alert_id, camera, at, raw in db.execute(sql, params):
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            excluded += 1
            continue
        if not isinstance(record, dict):
            excluded += 1
            continue
        eid = record.get('eventId')
        if not isinstance(eid, str) or not eid:
            excluded += 1
            continue
        if event_id and eid != event_id:
            continue
        if not isinstance(at, str) or not at:
            excluded += 1
            continue
        status = disposition_of(record)
        if disposition and status != disposition:
            continue
        fields = public_fields(record, eid)
        rows.append({
            'id': alert_id,
            'cameraId': camera or record.get('cameraId') or '',
            'cameraName': record.get('cameraName') or camera or '',
            'eventId': eid,
            'eventName': record.get('eventName') or EVENTS.get(eid, {}).get('name') or eid,
            'at': at,
            'image': record.get('image') or '',
            'video': record.get('video') or '',
            'detections': record.get('detections') if isinstance(record.get('detections'), list) else [],
            'alarm': bool(record.get('alarm')),
            'record': record,
            **fields,
        })
    return rows, excluded


def dashboard(database: Path | str, *, preset: str = 'today', start: str | None = None,
              end: str | None = None, camera_id: str | None = None, event_id: str | None = None,
              cameras: list[dict] | None = None, now: datetime | None = None) -> dict:
    begin, finish, used_preset = resolve_window(preset, start, end, now=now)
    if camera_id is not None and camera_id != '' and not isinstance(camera_id, str):
        raise DashboardError('摄像头编号无效')
    if event_id and event_id not in EVENTS:
        raise DashboardError('事件类型无效')
    camera_id = camera_id or None
    event_id = event_id or None
    kind = _bucket_kind(begin, finish)
    ensure_indexes(database)
    with closing(sqlite3.connect(Path(database), timeout=30)) as db:
        db.execute('BEGIN')
        try:
            rows, excluded = _load_rows(db, begin, finish, camera_id, event_id)
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK')
            raise

    by_type: dict[str, int] = {}
    by_camera: dict[str, dict] = {}
    trend_counts = {key: 0 for key in _iter_buckets(begin, finish, kind)}
    latest_at = None
    for row in rows:
        by_type[row['eventId']] = by_type.get(row['eventId'], 0) + 1
        bucket = by_camera.setdefault(row['cameraId'], {
            'cameraId': row['cameraId'], 'cameraName': row['cameraName'], 'count': 0})
        bucket['count'] += 1
        if row['cameraName']:
            bucket['cameraName'] = row['cameraName']
        key = _bucket_key(row['at'], kind)
        if key in trend_counts:
            trend_counts[key] += 1
        if latest_at is None or row['at'] > latest_at:
            latest_at = row['at']

    total = len(rows)
    high_risk_count = sum(1 for row in rows if row.get('riskLevel') == 'high')
    kpi = disposition_metrics(rows)
    type_items = sorted(
        ({'eventId': eid,
          'eventName': EVENTS.get(eid, {}).get('name') or eid,
          'count': count,
          'share': round(count / total, 4) if total else 0.0}
         for eid, count in by_type.items()),
        key=lambda item: (-item['count'], item['eventId']))
    top_type = None
    if type_items:
        leaders = [item for item in type_items if item['count'] == type_items[0]['count']]
        top_type = {
            'eventId': leaders[0]['eventId'],
            'eventName': leaders[0]['eventName'],
            'count': leaders[0]['count'],
            'tied': len(leaders) > 1,
            'tiedNames': [item['eventName'] for item in leaders] if len(leaders) > 1 else [],
        }

    camera_items = sorted(by_camera.values(), key=lambda item: (-item['count'], item['cameraId']))
    ranked = camera_items[:CAMERA_RANK_LIMIT]
    others = sum(item['count'] for item in camera_items[CAMERA_RANK_LIMIT:])
    if others:
        ranked.append({'cameraId': '', 'cameraName': '其余摄像头合计', 'count': others, 'remainder': True})

    live = cameras or []
    live_ids = {c.get('id') for c in live if c.get('id')}
    filter_cameras = []
    for cam in live:
        filter_cameras.append({
            'cameraId': cam.get('id'), 'cameraName': cam.get('name') or cam.get('id'),
            'location': str(cam.get('location') or '').strip(),
            'status': 'active' if cam.get('running') else 'configured',
            'state': cam.get('state') or ('running' if cam.get('running') else 'stopped'),
        })
    for item in camera_items:
        if item['cameraId'] not in live_ids:
            filter_cameras.append({
                'cameraId': item['cameraId'], 'cameraName': item['cameraName'] or item['cameraId'],
                'location': '',
                'status': 'deleted', 'state': 'deleted',
            })

    analysis = [{
        'cameraId': cam.get('id'),
        'cameraName': cam.get('name') or cam.get('id'),
        'state': cam.get('state') or ('running' if cam.get('running') else 'stopped'),
        'running': bool(cam.get('running')),
    } for cam in live]
    if camera_id:
        analysis = [item for item in analysis if item['cameraId'] == camera_id]

    recent = list(reversed(rows[-RECENT_LIMIT:]))
    recent_view = [{
        'id': row['id'],
        'at': row['at'],
        'capturedAt': row['record'].get('capturedAt') or row['at'],
        'eventId': row['eventId'],
        'eventName': row['eventName'],
        'cameraId': row['cameraId'],
        'cameraName': row['cameraName'],
        'image': row['image'],
        'detectionCount': len(row['detections']),
        'detections': row['detections'],
        'regions': row['record'].get('regions') if isinstance(row['record'].get('regions'), list) else [],
        'alarm': row['alarm'],
        'inferenceMs': row['record'].get('inferenceMs'),
    } for row in recent]

    return {
        'generatedAt': _iso(datetime.now(timezone.utc)),
        'timezone': 'UTC+8',
        'preset': used_preset,
        'range': {'from': _iso(begin), 'to': _iso(finish)},
        'bucket': kind,
        'filters': {'cameraId': camera_id or '', 'eventId': event_id or ''},
        'summary': {
            'totalEvents': total,
            'highRiskCount': high_risk_count,
            'cameraCount': len(by_camera),
            'typeCount': len(by_type),
            'topType': top_type,
            'latestAt': latest_at,
            **kpi,
        },
        'trend': [{'bucket': key, 'count': trend_counts[key]} for key in sorted(trend_counts)],
        'byType': type_items,
        'byCamera': ranked,
        'recent': recent_view,
        'analysis': analysis,
        'filterCameras': filter_cameras,
        'filterEvents': [
            {'eventId': eid, 'eventName': meta['name']}
            for eid, meta in EVENTS.items() if meta.get('alarm')
        ],
        'excludedRecords': excluded,
        'hints': _hints(live, total),
    }


def _camera_location_map(cameras: list[dict] | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for cam in cameras or []:
        if not isinstance(cam, dict):
            continue
        cid = cam.get('id') or cam.get('cameraId')
        if not cid:
            continue
        mapping[str(cid)] = str(cam.get('location') or '').strip()
    return mapping


def query_alerts(database: Path | str, *, preset: str = 'today', start: str | None = None,
                 end: str | None = None, camera_id: str | None = None, event_id: str | None = None,
                 disposition: str | None = None, risk_level: str | None = None,
                 location: str | None = None, cameras: list[dict] | None = None,
                 page: int = 1, page_size: int = 50,
                 now: datetime | None = None, sort: str = 'time') -> dict:
    begin, finish, used_preset = resolve_window(preset, start, end, now=now)
    if event_id and event_id not in EVENTS:
        raise DashboardError('事件类型无效')
    if disposition and disposition not in DISPOSITIONS:
        raise DashboardError('处置状态无效')
    if risk_level and risk_level not in RISK_LEVELS:
        raise DashboardError('风险级别无效')
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), MAX_PAGE_SIZE))
    camera_id = camera_id or None
    event_id = event_id or None
    disposition = disposition or None
    risk_level = risk_level or None
    location = (location or '').strip() or None
    locations = _camera_location_map(cameras)
    location_camera_ids = None
    if location:
        location_camera_ids = {
            cid for cid, loc in locations.items() if loc == location
        }
        if camera_id and camera_id not in location_camera_ids:
            # 摄像头与位置冲突时结果为空，仍走正常分页
            location_camera_ids = set()
    ensure_indexes(database)
    with closing(sqlite3.connect(Path(database), timeout=30)) as db:
        db.execute('BEGIN')
        try:
            rows, excluded = _load_rows(db, begin, finish, camera_id, event_id, disposition)
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK')
            raise
    if location_camera_ids is not None:
        rows = [
            row for row in rows
            if str(row.get('cameraId') or '') in location_camera_ids
        ]
    if risk_level:
        rows = [row for row in rows if row.get('riskLevel') == risk_level]
    for row in rows:
        row['location'] = locations.get(str(row.get('cameraId') or ''), '')
    # 库内按时间升序；默认时间序翻成最新优先。风险序：高→低，同风险稳定保持最新优先。
    rows = list(reversed(rows))
    if sort == 'risk':
        rows = sorted(rows, key=lambda row: risk_rank(row['riskLevel']))
    total = len(rows)
    start_index = (page - 1) * page_size
    slice_rows = rows[start_index:start_index + page_size]
    return {
        'generatedAt': _iso(datetime.now(timezone.utc)),
        'timezone': 'UTC+8',
        'preset': used_preset,
        'range': {'from': _iso(begin), 'to': _iso(finish)},
        'filters': {
            'cameraId': camera_id or '', 'eventId': event_id or '',
            'disposition': disposition or '',
            'riskLevel': risk_level or '',
            'location': location or '',
        },
        'page': page,
        'pageSize': page_size,
        'total': total,
        'excludedRecords': excluded,
        'rows': [{
            'id': row['id'],
            'at': row['at'],
            'eventId': row['eventId'],
            'eventName': row['eventName'],
            'cameraId': row['cameraId'],
            'cameraName': row['cameraName'],
            'location': row.get('location') or '',
            'image': row['image'],
            'video': row.get('video') or '',
            'detectionCount': len(row['detections']),
            'detections': row['detections'],
            'disposition': row['disposition'],
            'riskLevel': row['riskLevel'],
            'handledAt': row['handledAt'],
            'handledBy': row['handledBy'],
            'dispositionNote': row['dispositionNote'],
            'regions': row['record'].get('regions') if isinstance(row['record'].get('regions'), list) else [],
        } for row in slice_rows],
    }


def pending_summary(database: Path | str, *, now: datetime | None = None) -> dict:
    """未处置告警数量与最高风险，供值班弹窗。"""
    result = query_alerts(
        database, preset='last30d', disposition='pending', page=1, page_size=1,
        now=now, sort='risk')
    highest = ''
    if result['rows']:
        highest = result['rows'][0]['riskLevel']
    return {
        'pendingCount': result['total'],
        'highestRisk': highest,
        'generatedAt': result['generatedAt'],
    }


def _hints(cameras: list[dict], total: int) -> list[str]:
    messages = []
    if total == 0 and cameras:
        alarm_enabled = False
        detect_only = False
        for cam in cameras:
            for task in cam.get('tasks') or []:
                if not task.get('enabled', True):
                    continue
                event = EVENTS.get(task.get('eventId'), {})
                if event.get('alarm'):
                    alarm_enabled = True
                else:
                    detect_only = True
        if detect_only and not alarm_enabled:
            messages.append('当前启用的是检测场景；如需告警可配置区域入侵等报警场景')
    return messages
