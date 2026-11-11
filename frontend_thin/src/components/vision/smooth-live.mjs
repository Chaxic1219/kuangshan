/** 放大页：是否可用流畅底流（与推理解耦）。 */
export function canUseSmoothBase(preview) {
  if (!preview || typeof preview !== "object") return false;
  const kind = preview.kind;
  if (kind === "video") return Boolean(preview.url);
  if (kind === "webrtc") return Boolean(preview.url || preview.whep);
  return false;
}

/** 检测结果相对墙钟的落后毫秒；无效则返回 null。 */
export function detectionLagMs(record, now = Date.now()) {
  const at = record?.capturedAt;
  if (!at) return null;
  const t = Date.parse(at);
  if (!Number.isFinite(t)) return null;
  return Math.max(0, now - t);
}

/**
 * 延迟角标文案与级别。
 * @returns {{ text: string, level: 'quiet'|'info'|'warn' } | null}
 */
export function formatDetectionLag(ms) {
  if (ms == null || !Number.isFinite(ms)) return null;
  if (ms < 1000) return { text: "", level: "quiet" };
  const seconds = ms / 1000;
  const text =
    seconds >= 10
      ? `检测延迟 ${Math.round(seconds)}s`
      : `检测延迟 ${seconds.toFixed(1)}s`;
  return { text, level: ms >= 3000 ? "warn" : "info" };
}

/** 取 latest 中最新一条检测记录。 */
export function latestRecord(camera) {
  const rows = Object.values(camera?.latest || {});
  if (!rows.length) return null;
  return rows.sort((a, b) =>
    String(b.capturedAt || "").localeCompare(String(a.capturedAt || ""))
  )[0];
}
