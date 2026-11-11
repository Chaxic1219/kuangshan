# E1 告警处置闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为企业值班员提供未处置告警弹窗/提示、处置台（已处置/误报）与按处置状态查询，对齐企业篇手册 4.3～4.4。

**Architecture:** 在现有 `alerts` 表的 `record` JSON 上扩展 `disposition` 字段；新增查询筛选与处置写接口；前端新增处置台与查询状态筛，保留现有证据图。视频线索 E1 可占位。

**Tech Stack:** Python unittest、`alert_dashboard.py` / `console.py`、`frontend_thin` Vue3

**规格：** `docs/superpowers/specs/2026-10-28-enterprise-platform-upgrade-design.md`

---

### Task 1: 处置字段与后端 API

**Files:**
- Create/Modify: `analysis-service/alert_disposition.py`（纯函数：规范化、更新 record）
- Modify: `analysis-service/alert_dashboard.py`（query 支持 `disposition` 筛选；列表带回字段）
- Modify: `analysis-service/console.py`（`POST /api/alerts/dispose`；query 透传）
- Test: `analysis-service/tests/test_alert_disposition.py`

- [x] **Step 1:** 写失败测试：pending 默认、dispose→handled/false_positive、非法状态拒绝、query 按 disposition 过滤
- [x] **Step 2:** 实现 `normalize_disposition` / `apply_disposition`；旧记录无字段视为 `pending`
- [x] **Step 3:** 实现 dispose API 与 query 筛选；跑通 unittest

处置 record 字段约定：

```json
{
  "disposition": "pending|handled|false_positive",
  "handledAt": "<iso>",
  "handledBy": "<string>",
  "dispositionNote": "<optional>"
}
```

风险级别：由 `EVENTS` / 事件映射 `high|medium|low`（可简单表：入侵/火焰/烟雾/未戴帽等 high，其余 medium）。

### Task 2: 前端告警查询对齐处置态

**Files:**
- Modify: `frontend_thin/src/components/vision/AlertFilters.vue`、`alert-utils.mjs`
- Modify: `frontend_thin/src/views/vision/Alerts.vue`
- Test: 现有 `alert-utils` / 组件测试若有则更新

- [x] **Step 1:** 筛选增加处置状态；列表展示状态标签
- [x] **Step 2:** 详情可操作「处置完成」「误报」（或跳转处置台）

### Task 3: 处置台 + 未处置提示

**Files:**
- Create: `frontend_thin/src/views/vision/AlertDesk.vue`（处置台）
- Modify: `frontend_thin/src/router/modules/vision.ts`（菜单「告警处置」）
- Modify: 布局或 App 级小组件：未处置数量轮询 + 可选声音（可静音）

- [x] **Step 1:** 处置台按风险排序列出 pending，支持图线索切换，处置后下一条
- [x] **Step 2:** 处置台内未处置提示条 + 可选提示音（可静音）
- [x] **Step 3:** `tsc`/`vue-tsc` + node 测试通过；已重建 dist

### Task 4: 验收

- [x] API `pending-summary` 已通（当前环境有 pending 告警）
- [ ] 浏览器走通：处置台 → 处置/误报 → 查询按状态过滤（请主人点验）
- [ ] 提交仅在主人要求时进行
