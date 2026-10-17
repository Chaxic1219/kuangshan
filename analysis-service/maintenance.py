"""可再生运行文件维护：预览与执行两种模式。

只管理明确归本项目所有的运行文件：
- 当前场景 latest 引用帧始终保护；其余普通帧至少保留 60 秒；
- 已结束运行目录与冒烟目录保留 7 天，活动目录永不删除；
- 无摄像头引用的封面保留 24 小时；
- outbox 截图被 pending/dead 任一行引用即保留，无引用至少 24 小时；
- 事件数据库、证据图、权重与源视频不在本模块删除范围。

执行模式逐批有界；目录删除前规范化路径、拒绝符号链接/junction/重解析点；
引用查询失败时停止本批清理，绝不把“查询失败”当作“没有引用”。
"""
import argparse
import json
import re
import shutil
import sqlite3
import stat
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

GIB = 1024 ** 3
FRAME_MIN_AGE = 60
RUN_RETENTION_DAYS = 7
COVER_AGE_HOURS = 24
OUTBOX_AGE_HOURS = 24
ACTIVE_STATUS_SECONDS = 120
BATCH_LIMIT = 100
LOW_SPACE_WARN = 5 * GIB
LOW_SPACE_BLOCK = 1 * GIB
LOG_MAX_BYTES = 20 * 1024 * 1024
LOG_ROTATIONS = 5

RUN_NAME = re.compile(r'^[a-f0-9]{32}$')
SMOKE_NAME = re.compile(r'^smoke-[a-f0-9]{32}$')
COVER_NAME = re.compile(r'^[a-f0-9]{32}\.(jpg|jpeg|png)$', re.I)
FRAME_NAME = re.compile(r'^\d+\.(jpg|png)$', re.I)
MEDIA_NAME = re.compile(r'^.+\.(jpg|jpeg|png)$', re.I)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _is_reparse(path):
    try:
        info = path.lstat()
    except OSError:
        return False
    attributes = getattr(info, 'st_file_attributes', 0)
    flag = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
    return bool(attributes & flag) or stat.S_ISLNK(info.st_mode)


def _age_seconds(path):
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return 0.0


def _size(path):
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for child in path.rglob('*'):
            try:
                if child.is_file():
                    total += child.stat().st_size
            except OSError:
                continue
        return total
    except OSError:
        return 0


class Maintenance:
    def __init__(self, work, runs=None):
        self.work = Path(work).resolve()
        self.runtime = self.work/'runtime'
        self.covers = self.work/'covers'
        self.outbox = self.work/'outbox'
        self.evidence = self.work/'evidence'
        self.runs = Path(runs).resolve() if runs else None

    # ==================== 引用与保护 ====================

    def _status_files(self):
        if not self.runtime.is_dir():
            return []
        return list(self.runtime.glob('*/*/*/*/status.json'))

    def _load_json(self, path):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None

    def _scan_status(self):
        """返回 (活动 uuid 目录集合, latest 帧集合, 无法解析的状态文件)。"""
        active = set()
        latest = set()
        broken = []
        cutoff = time.time() - ACTIVE_STATUS_SECONDS
        for status_path in self._status_files():
            try:
                run_dir = status_path.parents[1]
                uuid_dir = status_path.parents[2]
            except IndexError:
                broken.append(str(status_path))
                continue
            data = self._load_json(status_path)
            if not isinstance(data, dict):
                broken.append(str(status_path))
                continue
            try:
                stamp = datetime.fromisoformat(str(data.get('at'))).timestamp()
            except (TypeError, ValueError):
                stamp = 0.0
            if stamp >= cutoff:
                active.add(uuid_dir.resolve())
            for record in (data.get('latest') or {}).values():
                if isinstance(record, dict) and record.get('snapshot'):
                    latest.add((run_dir/record['snapshot']).resolve())
        return active, latest, broken

    def _camera_covers(self):
        path = self.work/'cameras.json'
        if not path.is_file():
            return set(), None
        try:
            cameras = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return set(), '摄像头配置读取失败，本批不清理封面'
        covers = set()
        for camera in cameras if isinstance(cameras, list) else []:
            cover = (camera or {}).get('cover') if isinstance(camera, dict) else None
            if isinstance(cover, str) and cover.strip():
                covers.add(Path(cover).name)
        return covers, None

    def _outbox_snapshot_refs(self, db_path, lock=False):
        """读取 outbox 截图的绝对路径引用；错误向上抛出由调用方决定是否继续。"""
        refs = set()
        with closing(sqlite3.connect(db_path, timeout=2)) as conn:
            if lock:
                conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute('SELECT snapshot FROM outbox').fetchall()
            for (snapshot,) in rows:
                if snapshot:
                    refs.add(Path(snapshot).resolve())
        return refs

    # ==================== 候选规划 ====================

    def plan(self, active_dirs=None):
        active = {Path(item).resolve() for item in (active_dirs or [])}
        scanned_active, latest, broken = self._scan_status()
        active |= scanned_active
        candidates, protected, unknown, errors = [], [], [], []
        if broken:
            errors.append('有 %d 个运行状态文件无法解析，相关目录不视为可再生' % len(broken))

        candidates.extend(self._frame_candidates(latest, errors))
        candidates.extend(self._cover_candidates(protected, errors))
        candidates.extend(self._run_candidates(active, broken, unknown, errors))
        candidates.extend(self._smoke_candidates(errors))
        candidates.extend(self._outbox_media_candidates(protected, errors))
        return {
            'preview': True,
            'generatedAt': now_iso(),
            'candidates': candidates[:BATCH_LIMIT],
            'total': len(candidates),
            'protected': len(protected),
            'unknown': unknown,
            'errors': errors,
        }

    def _frame_candidates(self, latest, errors):
        result = []
        if not self.runtime.is_dir():
            return result
        for frames_dir in self.runtime.glob('*/*/*/*/frames'):
            try:
                files = list(frames_dir.iterdir())
            except OSError:
                errors.append('帧目录读取失败：%s' % frames_dir)
                continue
            for file in files:
                if not file.is_file():
                    continue
                if not FRAME_NAME.match(file.name):
                    continue
                if file.resolve() in latest:
                    continue
                if _age_seconds(file) < FRAME_MIN_AGE:
                    continue
                result.append({'path': str(file), 'kind': 'frame', 'size': _size(file),
                               'reason': '非 latest 引用且超过 %d 秒' % FRAME_MIN_AGE})
        return result

    def _cover_candidates(self, protected, errors):
        result = []
        if not self.covers.is_dir():
            return result
        covers, problem = self._camera_covers()
        if problem:
            errors.append(problem)
        for file in self.covers.iterdir():
            if not file.is_file():
                continue
            if not COVER_NAME.match(file.name):
                continue
            if file.name in covers or file.resolve() in protected:
                continue
            if _age_seconds(file) < COVER_AGE_HOURS * 3600:
                continue
            result.append({'path': str(file), 'kind': 'cover', 'size': _size(file),
                           'reason': '无摄像头引用且超过 %d 小时' % COVER_AGE_HOURS})
        return result

    def _run_candidates(self, active, broken, unknown, errors):
        result = []
        if not self.runtime.is_dir():
            return result
        broken_dirs = {Path(item).parents[2].resolve() for item in broken
                       if len(Path(item).parents) >= 3}
        retention = RUN_RETENTION_DAYS * 86400
        for camera_dir in self.runtime.iterdir():
            if not camera_dir.is_dir():
                unknown.append({'path': str(camera_dir), 'reason': '运行根下存在非目录条目'})
                continue
            for run_root in camera_dir.iterdir():
                if not run_root.is_dir():
                    unknown.append({'path': str(run_root), 'reason': '未知条目'})
                    continue
                resolved = run_root.resolve()
                if _is_reparse(run_root):
                    unknown.append({'path': str(run_root), 'reason': '目录是符号链接或重解析点'})
                    continue
                if not RUN_NAME.match(run_root.name):
                    unknown.append({'path': str(run_root), 'reason': '目录名不符合运行目录规则'})
                    continue
                if resolved in active or resolved in broken_dirs:
                    continue
                if _age_seconds(run_root) < retention:
                    continue
                problem = self._run_directory_problem(run_root)
                if problem:
                    unknown.append({'path': str(run_root), 'reason': problem})
                    continue
                result.append({'path': str(run_root), 'kind': 'run-dir', 'size': _size(run_root),
                               'reason': '已结束运行目录超过 %d 天' % RUN_RETENTION_DAYS})
        return result

    def _run_directory_problem(self, run_root):
        for child in run_root.rglob('*'):
            if _is_reparse(child):
                return '目录内含符号链接或重解析点'
            if child.is_file() and child.suffix.lower() in ('.db', '.sqlite', '.sqlite3'):
                return '目录内含未知数据库，不视为可再生'
        return ''

    def _smoke_candidates(self, errors):
        result = []
        if not self.runs or not self.runs.is_dir():
            return result
        retention = RUN_RETENTION_DAYS * 86400
        for item in self.runs.iterdir():
            if not item.is_dir():
                continue
            if not SMOKE_NAME.match(item.name):
                continue
            if _age_seconds(item) < retention:
                continue
            pending = False
            try:
                for db in item.rglob('outbox.db'):
                    try:
                        with closing(sqlite3.connect(db, timeout=2)) as conn:
                            count = conn.execute("SELECT COUNT(*) FROM outbox WHERE state IN ('pending','dead')").fetchone()[0]
                        if count:
                            pending = True
                            break
                    except sqlite3.Error:
                        pending = True
                        break
            except OSError:
                pending = True
            if pending:
                continue
            result.append({'path': str(item), 'kind': 'smoke-dir', 'size': _size(item),
                           'reason': '冒烟目录超过 %d 天且无待发送记录' % RUN_RETENTION_DAYS})
        return result

    def _outbox_media_candidates(self, protected, errors):
        result = []
        if not self.outbox.is_dir():
            return result
        for camera_dir in self.outbox.iterdir():
            if not camera_dir.is_dir():
                continue
            media = camera_dir/'media'
            db = camera_dir/'outbox.db'
            if not media.is_dir():
                continue
            refs = set()
            if db.is_file():
                try:
                    refs = self._outbox_snapshot_refs(db)
                except sqlite3.Error as exc:
                    errors.append('outbox 引用查询失败（%s）：%s，本批不清理该队列' % (db.name, exc))
                    continue
            for file in media.iterdir():
                if not file.is_file() or not MEDIA_NAME.match(file.name):
                    continue
                if file.resolve() in refs or file.resolve() in protected:
                    continue
                if _age_seconds(file) < OUTBOX_AGE_HOURS * 3600:
                    continue
                result.append({'path': str(file), 'kind': 'outbox-media', 'size': _size(file),
                               'reason': 'outbox 无引用且超过 %d 小时' % OUTBOX_AGE_HOURS})
        return result

    # ==================== 执行 ====================

    def execute(self, active_dirs=None, limit=BATCH_LIMIT):
        preview = self.plan(active_dirs)
        report = {'preview': False, 'generatedAt': now_iso(),
                  'deleted': [], 'skipped': [], 'errors': list(preview['errors']),
                  'unknown': preview['unknown'], 'protected': preview['protected']}
        if report['errors']:
            # 引用查询失败时停止本批清理，保守处理。
            report['stopped'] = '引用检查失败，本批未执行删除'
            return report
        candidates = preview['candidates'][:max(1, min(int(limit), BATCH_LIMIT))]
        outbox_media = [item for item in candidates if item['kind'] == 'outbox-media']
        others = [item for item in candidates if item['kind'] != 'outbox-media']
        for item in others:
            outcome = self._delete(item)
            (report['deleted'] if outcome.get('ok') else report['skipped']).append(outcome)
        if outbox_media:
            report['outbox'] = self._delete_outbox_media(outbox_media)
        return report

    def _outbox_media_safe(self, file, camera_dir):
        """outbox 截图删除边界：根目录、摄像头目录与祖先重解析点。"""
        try:
            outbox_root = self.outbox.resolve()
            camera_resolved = camera_dir.resolve()
            file_resolved = file.resolve()
        except OSError:
            return False, '路径无法解析，拒绝删除'
        if not camera_resolved.is_relative_to(outbox_root):
            return False, '截图目录不在 outbox 根内，拒绝删除'
        if not file_resolved.is_relative_to(camera_resolved):
            return False, '截图不在对应摄像头目录内，拒绝删除'
        for path in (camera_dir, file.parent, file):
            if _is_reparse(path):
                return False, '符号链接或 junction，拒绝删除'
        return True, ''

    def _delete_outbox_media(self, items):
        """按摄像头在 BEGIN IMMEDIATE 事务内重新检查引用与删除边界后删除。"""
        result = {'deleted': [], 'skipped': [], 'errors': []}
        grouped = {}
        for item in items:
            grouped.setdefault(Path(item['path']).parent.parent, []).append(Path(item['path']))
        for camera_dir, files in grouped.items():
            # 打开数据库前先验证目录边界，避免 junction 指向外部队列或文件系统。
            safe, reason = self._outbox_media_safe(files[0], camera_dir)
            if not safe:
                result['skipped'].extend({'path': str(file), 'reason': reason} for file in files)
                continue
            db = camera_dir/'outbox.db'
            try:
                with closing(sqlite3.connect(db, timeout=2)) as conn:
                    conn.execute('BEGIN IMMEDIATE')
                    refs = {Path(row[0]).resolve() for row in conn.execute(
                        'SELECT snapshot FROM outbox').fetchall() if row[0]}
                    for file in files:
                        safe, reason = self._outbox_media_safe(file, camera_dir)
                        if not safe:
                            result['skipped'].append({'path': str(file), 'reason': reason})
                            continue
                        if file.resolve() in refs:
                            result['skipped'].append({'path': str(file), 'reason': '清理期间出现引用'})
                            continue
                        try:
                            size = file.stat().st_size
                            file.unlink()
                            result['deleted'].append({'path': str(file), 'kind': 'outbox-media',
                                                      'size': size, 'reason': '无引用且超过保留期'})
                        except OSError as exc:
                            result['skipped'].append({'path': str(file), 'reason': str(exc)})
                    conn.commit()
            except sqlite3.Error as exc:
                result['errors'].append('outbox 清理被跳过（%s）：%s' % (camera_dir.name, exc))
        return result

    def _roots(self, kind):
        if kind == 'run-dir':
            return self.runtime
        if kind == 'smoke-dir':
            return self.runs
        if kind == 'frame':
            return self.runtime
        if kind == 'cover':
            return self.covers
        if kind == 'outbox-media':
            return self.outbox
        return None

    def _delete(self, item):
        path = Path(item['path'])
        root = self._roots(item['kind'])
        if root is None:
            return {'path': str(path), 'reason': '未知清理类型，仅报告', 'ok': False}
        try:
            resolved = path.resolve()
            root_resolved = root.resolve()
        except OSError:
            return {'path': str(path), 'reason': '路径无法解析，仅报告', 'ok': False}
        if not resolved.is_relative_to(root_resolved):
            return {'path': str(path), 'reason': '路径不在允许根目录内，拒绝删除', 'ok': False}
        if _is_reparse(path):
            return {'path': str(path), 'reason': '符号链接或 junction，拒绝删除', 'ok': False}
        try:
            size = _size(path)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return {'path': str(path), 'kind': item['kind'], 'size': size,
                    'reason': item['reason'], 'ok': True}
        except OSError as exc:
            return {'path': str(path), 'reason': '删除失败（可能被占用）：%s' % exc, 'ok': False}

    # ==================== 磁盘空间 ====================

    def disk_status(self, extra_paths=()):
        items = []
        level = 'ok'
        rank = {'ok': 0, 'warn': 1, 'blocked': 2}
        for path in [self.work, *[Path(item) for item in extra_paths if item]]:
            try:
                usage = shutil.disk_usage(str(path))
            except OSError:
                continue
            item_level = ('blocked' if usage.free < LOW_SPACE_BLOCK
                          else 'warn' if usage.free < LOW_SPACE_WARN else 'ok')
            if rank[item_level] > rank[level]:
                level = item_level
            items.append({'path': str(path), 'freeGiB': round(usage.free / GIB, 2),
                          'totalGiB': round(usage.total / GIB, 2), 'level': item_level})
        free = min((item['freeGiB'] for item in items), default=0)
        return {'level': level, 'freeGiB': free, 'items': items}


def main(argv=None):
    parser = argparse.ArgumentParser(description='可再生运行文件清理（默认仅预览）')
    parser.add_argument('--work', type=Path,
                        default=Path(__file__).resolve().parent/'data')
    parser.add_argument('--runs', type=Path,
                        default=Path(__file__).resolve().parent/'runs')
    parser.add_argument('--execute', action='store_true', help='执行删除；默认只预览')
    parser.add_argument('--limit', type=int, default=BATCH_LIMIT)
    args = parser.parse_args(argv)
    maintenance = Maintenance(args.work, args.runs)
    if args.execute:
        report = maintenance.execute(limit=args.limit)
    else:
        report = maintenance.plan()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
