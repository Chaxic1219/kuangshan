# 检测选框统一样式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实时监控烧录框与告警处置/查询叠框使用同一套颜色与「中文 + 置信度」文案。

**Architecture:** 后端新增 `overlay_style.py`（映射、着色、caption、中文绘制）；`annotate_frame` 改用该模块。前端在 `frame.mjs` 增加同等 `formatDetectionCaption` / `detectionStroke`，`DetectionFrame.vue` 调用之。两侧映射表内容对齐规格清单。

**Tech Stack:** Python/OpenCV（+ Pillow 或系统 TTF 绘中文）、Vue3、`frame.mjs` 单测。

**Spec:** `docs/superpowers/specs/2026-10-29-detection-overlay-unified-style-design.md`

---

### Task 1: 后端 overlay_style + 单测

**Files:**
- Create: `analysis-service/overlay_style.py`
- Create/Modify: `analysis-service/tests/test_overlay_style.py`
- Modify: `analysis-service/service.py`（`annotate_frame`）
- Modify: `analysis-service/tests/test_annotate_live.py`

- [ ] caption / stroke 纯函数单测（NO-Hardhat → 未戴安全帽 90%；报警色 BGR）
- [ ] 实现 `overlay_style.py`（映射、颜色、中文 putText）
- [ ] `annotate_frame` 接入；相关 unittest 通过

### Task 2: 前端 frame 助手 + DetectionFrame

**Files:**
- Modify: `frontend_thin/src/components/vision/frame.mjs`
- Modify: `frontend_thin/src/components/vision/frame.test.mjs`
- Modify: `frontend_thin/src/components/vision/DetectionFrame.vue`

- [ ] `formatDetectionCaption` / `detectionStroke` 单测
- [ ] DetectionFrame 使用统一助手（颜色规则与 caption）
- [ ] `npm run build` 通过

### Task 3: 验证

- [ ] 后端相关 unittest + 前端 frame 测试 + 构建

**说明：** 本环境 git author 未配置时跳过 commit，规格与计划文件保留在工作区。
