# E1 收尾 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 补齐企业篇手册 4.3～4.4 残留：全站未处置提示、查询按风险级别、详情上下条、处置备注。

**Architecture:** 后端 `query_alerts` 增加 `riskLevel` 筛选；前端布局挂载全局 pending 提示条；告警查询证据弹窗支持同页上下条；处置台提交可选 `note`（API 已支持）。

**Tech Stack:** Python unittest、`alert_dashboard.py`、`frontend_thin` Vue3

---

### Task 1: 查询按风险级别

**Files:** `analysis-service/alert_dashboard.py`、`console.py`、`tests/test_alert_disposition.py`；`AlertFilters.vue`、`alert-utils.mjs`、`Alerts.vue`

- [x] 测试 + 实现 `riskLevel` 筛选并透传到前端筛选项

### Task 2: 详情上下条 + 处置备注

**Files:** `Alerts.vue`、`AlertDesk.vue`

- [x] 证据弹窗上一条/下一条；处置台备注输入并随 dispose 提交

### Task 3: 全站未处置提示

**Files:** 新建 `PendingAlertBanner.vue`；挂到 `layout/index.vue`

- [x] 轮询 pending-summary；可静音；点进处置台；处置台页弱化或隐藏避免重复

### Task 4: 构建验收

- [x] unittest + `pnpm build`；服务可用
