export const presets = [
  ["today", "今日"],
  ["last24h", "最近 24 小时"],
  ["last7d", "近 7 天"],
  ["last30d", "近 30 天"],
  ["custom", "自定义"]
];

export function wallTime(value) {
  const n = Date.parse(value);
  return Number.isFinite(n)
    ? new Date(n + 8 * 3600000).toISOString().slice(0, 23)
    : "";
}

export function fromWallTime(value) {
  const n = Date.parse(`${value}+08:00`);
  if (!Number.isFinite(n)) throw new Error("请填写有效的开始和结束时间");
  return new Date(n).toISOString();
}

export function validateRange(from, to) {
  const a = Date.parse(from),
    b = Date.parse(to);
  if (!Number.isFinite(a) || !Number.isFinite(b))
    throw new Error("请填写有效的开始和结束时间");
  if (b <= a) throw new Error("结束时间必须晚于开始时间");
  if (b - a > 90 * 86400000) throw new Error("单次查询最长 90 天");
  return { from, to };
}

export function alertQuery(filters, range = null) {
  const p = new URLSearchParams();
  p.set("preset", range ? "custom" : filters.preset || "today");
  if (range || filters.preset === "custom") {
    const r = range || filters;
    validateRange(r.from, r.to);
    p.set("from", r.from);
    p.set("to", r.to);
  }
  if (filters.cameraId) p.set("cameraId", filters.cameraId);
  if (filters.location) p.set("location", filters.location);
  if (filters.eventId) p.set("eventId", filters.eventId);
  if (filters.disposition) p.set("disposition", filters.disposition);
  if (filters.riskLevel) p.set("riskLevel", filters.riskLevel);
  if (filters.sort) p.set("sort", filters.sort);
  return p;
}

export const dispositionLabels = {
  pending: "未处置",
  handled: "已处置",
  false_positive: "误报"
};

export const riskLabels = {
  high: "高",
  medium: "中",
  low: "低"
};


export function dateText(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return "—";
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hour12: false
  });
}

export function trendPoints(rows) {
  const max = Math.max(1, ...rows.map(r => Number(r.count) || 0));
  return rows.map((r, i) => ({
    ...r,
    x: 32 + (i * 576) / Math.max(1, rows.length - 1),
    y: 170 - ((Number(r.count) || 0) / max) * 140
  }));
}

export function bucketRange(bucket, kind, range) {
  const start = Date.parse(`${bucket}${kind === "day" ? "T00:00" : ""}+08:00`);
  const end = start + (kind === "day" ? 86400000 : 3600000);
  return validateRange(
    Date.parse(range.from) >= start
      ? range.from
      : new Date(start).toISOString(),
    Date.parse(range.to) <= end ? range.to : new Date(end).toISOString()
  );
}

export const stateLabels = {
  running: "分析中",
  streaming: "已取流",
  connecting: "连接中",
  starting: "启动中",
  reconnecting: "重连中",
  stopped: "未运行",
  error: "故障",
  backoff: "自动恢复中",
  failed: "已失败",
  deleted: "已删除"
};
