"""告警视频线索：将近几秒分析帧合成短 MP4。"""
from __future__ import annotations

from pathlib import Path

import cv2

# 告警前短窗：最多约 45 帧（约 2～3 秒量级，视分析节拍而定）
MAX_CLIP_FRAMES = 45
DEFAULT_FPS = 8.0


def collect_recent_frames(frames_dir: Path | str, seq: int,
                          *, max_frames: int = MAX_CLIP_FRAMES) -> list[Path]:
    """按序号收集 [seq-max_frames+1, seq] 中已落盘的 jpg。"""
    folder = Path(frames_dir)
    if not folder.is_dir() or seq < 1:
        return []
    limit = max(1, int(max_frames or MAX_CLIP_FRAMES))
    start = max(1, int(seq) - limit + 1)
    paths: list[Path] = []
    for index in range(start, int(seq) + 1):
        path = folder / f'{index}.jpg'
        if path.is_file():
            paths.append(path)
    return paths


def write_evidence_clip(frame_paths: list[Path], output: Path | str,
                        *, fps: float = DEFAULT_FPS) -> bool:
    """将 jpg 序列写成 mp4；失败返回 False，不抛到告警主路径。"""
    paths = [Path(p) for p in frame_paths if Path(p).is_file()]
    if len(paths) < 2:
        return False
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    first = cv2.imread(str(paths[0]))
    if first is None:
        return False
    height, width = first.shape[:2]
    if width < 2 or height < 2:
        return False
    rate = float(fps) if fps and fps > 0 else DEFAULT_FPS
    # mp4v 在 macOS/Windows OpenCV 上较通用；写到临时文件再替换。
    tmp = out.with_suffix(out.suffix + '.tmp.mp4')
    writer = None
    try:
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(str(tmp), fourcc, rate, (width, height))
        if not writer.isOpened():
            return False
        for path in paths:
            frame = cv2.imread(str(path))
            if frame is None:
                continue
            if frame.shape[1] != width or frame.shape[0] != height:
                frame = cv2.resize(frame, (width, height))
            writer.write(frame)
        writer.release()
        writer = None
        if not tmp.is_file() or tmp.stat().st_size < 64:
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(out)
        return out.is_file()
    except (OSError, cv2.error, ValueError, TypeError):
        try:
            if writer is not None:
                writer.release()
        except Exception:
            pass
        tmp.unlink(missing_ok=True)
        return False
    finally:
        if writer is not None:
            try:
                writer.release()
            except Exception:
                pass
