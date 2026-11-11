import assert from "node:assert/strict";
import { describe, it } from "node:test";
import {
  canUseSmoothBase,
  detectionLagMs,
  formatDetectionLag,
  latestRecord
} from "./smooth-live.mjs";

describe("canUseSmoothBase", () => {
  it("accepts video and webrtc with url", () => {
    assert.equal(canUseSmoothBase({ kind: "video", url: "/preview/a" }), true);
    assert.equal(
      canUseSmoothBase({ kind: "webrtc", url: "http://x", whep: "http://x/whep" }),
      true
    );
  });
  it("rejects frames and empty", () => {
    assert.equal(canUseSmoothBase({ kind: "frames" }), false);
    assert.equal(canUseSmoothBase(null), false);
    assert.equal(canUseSmoothBase({ kind: "video" }), false);
  });
});

describe("detectionLagMs / formatDetectionLag", () => {
  it("computes lag and formats thresholds", () => {
    const now = Date.parse("2026-11-11T01:00:00.000Z");
    assert.equal(
      detectionLagMs({ capturedAt: "2026-11-11T00:59:59.500Z" }, now),
      500
    );
    assert.equal(formatDetectionLag(500)?.level, "quiet");
    assert.equal(formatDetectionLag(1500)?.level, "info");
    assert.match(formatDetectionLag(1500)?.text || "", /检测延迟/);
    assert.equal(formatDetectionLag(4000)?.level, "warn");
  });
});

describe("latestRecord", () => {
  it("picks newest by capturedAt", () => {
    const row = latestRecord({
      latest: {
        a: { capturedAt: "2026-11-11T00:00:01.000Z", id: "a" },
        b: { capturedAt: "2026-11-11T00:00:03.000Z", id: "b" }
      }
    });
    assert.equal(row.id, "b");
  });
});
