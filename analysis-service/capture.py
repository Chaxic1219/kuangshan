"""隔离进程中抓取视频首帧，调用者负责整体超时。"""
import json
from pathlib import Path
import sys
import av
import cv2


def capture(source, target):
    av.logging.set_level(av.logging.PANIC)
    with av.open(source, options={'rtsp_transport': 'tcp'} if source.startswith('rtsp') else {},
                 timeout=(4., 4.)) as container:
        frame = next(container.decode(video=0)).to_ndarray(format='bgr24')
        h, w = frame.shape[:2]
        if w > 1280:
            frame = cv2.resize(frame, (1280, round(h*1280/w)))
        ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise ValueError('封面编码失败')
        Path(target).write_bytes(encoded.tobytes())


if __name__ == '__main__':
    try:
        cfg = json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
        capture(cfg['url'], cfg['target'])
    except Exception:
        sys.exit(1)
