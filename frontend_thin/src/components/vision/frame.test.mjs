import test from "node:test";
import assert from "node:assert/strict";
import {
  pointInImage,
  createFrameGate,
  frameRegions,
  isStaleFrame,
  detectionLabel,
  detectionStroke,
  formatDetectionCaption,
  ALARM_STROKE,
  FOLLOW_STROKE
} from "./frame.mjs";

test("实时记录按摄像头任务区域显示，历史证据保留冻结区域", () => {
  const record = { eventId: "intrusion", regions: [{ id: "old" }] };
  const camera = {
    tasks: [{ eventId: "intrusion", regionIds: ["on", "off"] }],
    regions: [
      { id: "on", enabled: true },
      { id: "off", enabled: false },
      { id: "other" }
    ]
  };
  assert.deepEqual(frameRegions(camera, record), [{ id: "on", enabled: true }]);
  assert.deepEqual(frameRegions(undefined, record), [{ id: "old" }]);
  assert.deepEqual(frameRegions(camera, record, []), []);
});
test("历史证据永不标为停更，实时画面按年龄与生命周期判断", () => {
  const record = { capturedAt: "2026-01-01T00:00:00Z" };
  const now = Date.parse(record.capturedAt) + 31000;
  assert.equal(isStaleFrame(undefined, record, now), false);
  assert.equal(isStaleFrame({ state: "running" }, record, now), true);
  assert.equal(
    isStaleFrame(
      { state: "running", lifecycle: "backoff" },
      record,
      now - 31000
    ),
    true
  );
  assert.equal(isStaleFrame({ state: "running" }, record, now - 31000), false);
});
test("检测类别继承中文映射并兼容小写装备类别", () => {
  assert.equal(detectionLabel("person"), "人员");
  assert.equal(detectionLabel("NO-Hardhat"), "未戴安全帽");
  assert.equal(detectionLabel("no-hardhat"), "未戴安全帽");
  assert.equal(detectionLabel("custom"), "custom");
});

test("选框文案为中文加置信度，颜色按 alarmable 优先", () => {
  assert.equal(
    formatDetectionCaption({ label: "NO-Hardhat", confidence: 0.9 }),
    "未戴安全帽 90%"
  );
  assert.equal(
    formatDetectionCaption({ label: "hardhat", confidence: 0.87 }),
    "安全帽 87%"
  );
  assert.equal(detectionStroke({ alarmable: true }), ALARM_STROKE);
  assert.equal(detectionStroke({ alarmable: false, inRegion: true }), FOLLOW_STROKE);
  assert.equal(detectionStroke({}), ALARM_STROKE);
});

test("宽画面留白不纳入坐标，缩放后归一化坐标保持一致", () => {
  assert.equal(
    pointInImage(
      100,
      10,
      { left: 0, top: 0, width: 200, height: 200 },
      400,
      200
    ),
    null
  );
  assert.deepEqual(
    pointInImage(
      100,
      100,
      { left: 0, top: 0, width: 200, height: 200 },
      400,
      200
    ),
    [0.5, 0.5]
  );
  assert.deepEqual(
    pointInImage(
      200,
      200,
      { left: 0, top: 0, width: 400, height: 400 },
      400,
      200
    ),
    [0.5, 0.5]
  );
});
test("纵画面、偏移容器与边缘拖动", () => {
  assert.deepEqual(
    pointInImage(
      110,
      120,
      { left: 10, top: 20, width: 200, height: 200 },
      100,
      200
    ),
    [0.5, 0.5]
  );
  assert.equal(
    pointInImage(
      10,
      120,
      { left: 10, top: 20, width: 200, height: 200 },
      100,
      200
    ),
    null
  );
  assert.deepEqual(
    pointInImage(
      0,
      300,
      { left: 10, top: 20, width: 200, height: 200 },
      100,
      200,
      true
    ),
    [0, 1]
  );
});
test("旧图晚到与卸载取消都不能提交", () => {
  const gate = createFrameGate();
  const old = gate.next();
  const current = gate.next();
  assert.equal(gate.accepts(old), false);
  assert.equal(gate.accepts(current), true);
  gate.next();
  assert.equal(gate.accepts(current), false);
});
