export function pointInImage(x, y, rect, width, height, clamp = false) {
  if (!width || !height || !rect.width || !rect.height) return null;
  const scale = Math.min(rect.width / width, rect.height / height);
  const w = width * scale,
    h = height * scale;
  const px = (x - rect.left - (rect.width - w) / 2) / w;
  const py = (y - rect.top - (rect.height - h) / 2) / h;
  if (!clamp && (px < 0 || px > 1 || py < 0 || py > 1)) return null;
  return [Math.max(0, Math.min(1, px)), Math.max(0, Math.min(1, py))];
}

export function createFrameGate() {
  let sequence = 0;
  return { next: () => ++sequence, accepts: value => value === sequence };
}

export function frameRegions(camera, record, regions) {
  if (regions !== undefined) return regions;
  if (!camera) return record?.regions || [];
  const task = camera.tasks?.find(task => task.eventId === record?.eventId);
  return (camera.regions || []).filter(
    zone => zone.enabled !== false && task?.regionIds?.includes(zone.id)
  );
}

export function isStaleFrame(camera, record, now = Date.now()) {
  if (!camera || !record?.capturedAt) return false;
  return (
    now - Date.parse(record.capturedAt) > 30000 ||
    [camera.state, camera.lifecycle].some(state =>
      ["reconnecting", "error", "backoff", "failed"].includes(state)
    )
  );
}

const labels = {
  person: "人员",
  car: "汽车",
  truck: "卡车",
  bus: "公交车",
  motorcycle: "摩托车",
  bicycle: "自行车",
  fire: "火焰",
  smoke: "烟雾",
  hardhat: "安全帽",
  "no-hardhat": "未戴安全帽",
  "safety vest": "反光衣",
  "no-safety vest": "未穿反光衣",
  mask: "口罩",
  "no-mask": "未戴口罩",
  vehicle: "车辆",
  machinery: "机械",
  过暗: "过暗",
  过亮: "过亮"
};

export const ALARM_STROKE = "#ff8066";
export const FOLLOW_STROKE = "#55eac4";

export function detectionLabel(label) {
  const raw = label == null ? "" : String(label);
  const mapped = labels[raw.toLowerCase()];
  if (mapped) return mapped;
  if (/[\u4e00-\u9fff]/.test(raw)) return raw;
  return raw;
}

/** 与后端 overlay_style 一致：有 alarmable 用之；否则 inRegion；都无默认报警色。 */
export function isAlarmStroke(detection) {
  const item = detection && typeof detection === "object" ? detection : {};
  if (Object.prototype.hasOwnProperty.call(item, "alarmable")) {
    return !!item.alarmable;
  }
  if (Object.prototype.hasOwnProperty.call(item, "inRegion")) {
    return !!item.inRegion;
  }
  return true;
}

export function detectionStroke(detection) {
  return isAlarmStroke(detection) ? ALARM_STROKE : FOLLOW_STROKE;
}

export function formatDetectionCaption(detection) {
  const item = detection && typeof detection === "object" ? detection : {};
  const name = detectionLabel(item.label);
  const conf = item.confidence;
  if (typeof conf === "number" && Number.isFinite(conf)) {
    return `${name} ${Math.round(conf * 100)}%`;
  }
  return name;
}
