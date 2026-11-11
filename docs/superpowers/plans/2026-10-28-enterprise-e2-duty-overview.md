# E2 值班监控总览 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 登录后进入手册式「监控总览」：近 7 日累计与高风险数、周期切换、类型占比饼图、紧急联系人 CRUD。

**Architecture:** 复用 `/api/alerts/dashboard` 增加 `summary.highRiskCount`；紧急联系人存 `data/contacts.json`，提供 GET/PUT `/api/contacts`。前端新增 `Overview.vue` 作为值班首页，保留现有「告警大屏」给运维。

**Tech Stack:** Python unittest、`alert_dashboard.py` / `console.py`、`frontend_thin` Vue3

**规格：** `docs/superpowers/specs/2026-10-28-enterprise-platform-upgrade-design.md` §5 E2

---

### Task 1: 仪表盘高风险计数

**Files:**
- Modify: `analysis-service/alert_dashboard.py`
- Test: `analysis-service/tests/test_alert_dashboard.py`

- [x] **Step 1:** 失败测试：周期内 high 风险告警计入 `summary.highRiskCount`
- [x] **Step 2:** 实现计数并跑通 unittest

### Task 2: 紧急联系人 API

**Files:**
- Create: `analysis-service/contacts.py`
- Modify: `analysis-service/console.py`（GET/PUT `/api/contacts`）
- Test: `analysis-service/tests/test_contacts.py`

- [x] **Step 1:** 失败测试：读写多组联系人（姓名/机构/电话），非法项拒绝
- [x] **Step 2:** JSON 文件持久化 + 控制台路由

### Task 3: 前端监控总览页

**Files:**
- Create: `frontend_thin/src/views/vision/Overview.vue`
- Modify: `frontend_thin/src/router/modules/vision.ts`、`home.ts`
- Rebuild: `frontend_thin` dist

- [x] **Step 1:** 总览页：周期切换、累计/高风险卡片、饼图、联系人编辑
- [x] **Step 2:** 菜单「监控总览」rank 优先；登录默认跳转总览
- [x] **Step 3:** 构建 dist；冒烟 API

### Task 4: 验收

- [x] 打开 `/ui/#/overview`：近 7 日数据与联系人可保存（请主人浏览器点验）
- [ ] 提交仅在主人要求时进行
