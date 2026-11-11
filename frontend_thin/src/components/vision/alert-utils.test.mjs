import test from "node:test";
import assert from "node:assert/strict";
import {
  wallTime,
  fromWallTime,
  validateRange,
  alertQuery,
  trendPoints,
  bucketRange
} from "./alert-utils.mjs";

test("UTC+8 输入与服务器微秒精度以毫秒往返，不依赖系统时区", () => {
  assert.equal(wallTime("2026-10-24T16:00:00.123Z"), "2026-10-25T00:00:00.123");
  assert.equal(
    fromWallTime("2026-10-25T00:00:00.123"),
    "2026-10-24T16:00:00.123Z"
  );
});
test("半开范围要求有序且最多90天", () => {
  assert.throws(() => validateRange("bad", "bad"));
  assert.throws(() => validateRange("2026-01-01", "2026-01-01"));
  assert.throws(() => validateRange("2026-01-01", "2026-04-02"));
  assert.doesNotThrow(() => validateRange("2026-01-01", "2026-04-01"));
});
test("跳转与导出保留精确时间及全部筛选", () => {
  const range = {
    from: "2026-10-25T00:00:00.123456+00:00",
    to: "2026-10-25T01:02:03.456789+00:00"
  };
  const p = alertQuery(
    {
      preset: "today",
      cameraId: "摄像头&1",
      eventId: "intrusion",
      disposition: "pending",
      riskLevel: "high"
    },
    range
  );
  assert.equal(p.get("preset"), "custom");
  assert.equal(p.get("from"), range.from);
  assert.equal(p.get("to"), range.to);
  assert.equal(p.get("cameraId"), "摄像头&1");
  assert.equal(p.get("eventId"), "intrusion");
  assert.equal(p.get("disposition"), "pending");
  assert.equal(p.get("riskLevel"), "high");
});
test("趋势空值和单点坐标有限，峰值位于顶部", () => {
  assert.deepEqual(trendPoints([]), []);
  assert.equal(trendPoints([{ count: 0 }])[0].y, 170);
  const p = trendPoints([{ count: 0 }, { count: 10 }]);
  assert.equal(p[1].y, 30);
  assert.equal(p[1].x, 608);
});
test("图表钻取按UTC+8小时并裁剪至真实半开范围", () => {
  assert.deepEqual(
    bucketRange("2026-10-25T08:00", "hour", {
      from: "2026-10-25T00:30:00.123456Z",
      to: "2026-10-25T02:00:00Z"
    }),
    { from: "2026-10-25T00:30:00.123456Z", to: "2026-10-25T01:00:00.000Z" }
  );
});
