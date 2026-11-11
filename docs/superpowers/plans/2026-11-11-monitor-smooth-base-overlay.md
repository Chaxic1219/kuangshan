# 监控放大页流畅底流 + 叠框 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实时监控放大页用流畅 WebRTC/原视频作底流，SVG 叠检测框；底流失败回退 MJPEG。

**Architecture:** 新增 `SmoothLive`：优先 `LivePlayer` + SVG；`preview` 不可用或播放失败时改用 `AnnotatedLive`。仅改 `Monitor.vue` 放大区。

**Tech Stack:** Vue 3、现有 `LivePlayer` / `AnnotatedLive` / `frame.mjs`

**Spec:** `docs/superpowers/specs/2026-11-11-monitor-smooth-base-overlay-design.md`

---

### Task 1: 纯函数 + 单测

**Files:**
- Create: `frontend_thin/src/components/vision/smooth-live.mjs`
- Create: `frontend_thin/src/components/vision/smooth-live.test.mjs`

- [ ] `canUseSmoothBase(preview)`、`detectionLagMs(record, now)`、`formatDetectionLag(ms)`
- [ ] node 测试通过

### Task 2: LivePlayer 可上报失败

**Files:**
- Modify: `frontend_thin/src/components/vision/LivePlayer.vue`

- [ ] `emit('fail', message)`；`embedded` 时不展示 iframe/底部 notice（由父组件回退）

### Task 3: SmoothLive 组件

**Files:**
- Create: `frontend_thin/src/components/vision/SmoothLive.vue`

- [ ] 底流 + SVG 叠层 + 延迟角标；失败锁定回退至离开放大页/换摄像头

### Task 4: 接入 Monitor

**Files:**
- Modify: `frontend_thin/src/views/vision/Monitor.vue`

- [ ] 放大区改用 `SmoothLive`
- [ ] `npm run build` 通过
