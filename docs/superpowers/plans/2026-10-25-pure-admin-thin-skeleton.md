# Pure Admin Thin 完整骨架重装实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 干净重装官方 pure-admin-thin 骨架，登录 admin/admin，侧栏仅五个矿山业务标签页，复用现有 mine 业务与 Python `/ui/` 托管。

**Architecture:** 备份 mine 业务 → 以固定上游提交覆盖骨架 → 按官方 `router/modules` 注册业务菜单 → 恢复 fake 登录与开发代理 → 构建并由 analysis-service 同源托管。

**Tech Stack:** Vue 3、Pure Admin Thin 6.2、Element Plus、Pinia、Vite、vite-plugin-fake-server、Python `frontend_hosting.py`

**Spec:** `docs/superpowers/specs/2026-10-25-pure-admin-thin-skeleton-design.md`

**活动工程目录：** `frontend_thin/`（因原 `frontend/` 被进程占用无法整体替换）

---

### Task 1: 备份矿山业务并拉取干净 thin — 完成

- [x] 备份到 `_mine_backup/`
- [x] 官方 thin 固定提交 `f0ff132` 落到 `frontend_thin/`
- [x] `pnpm install`
- [x] 登录页、mock、home 模块存在

### Task 2: 迁回业务并注册官方风格菜单 — 完成

- [x] 复原 mine 业务
- [x] `src/router/modules/mine.ts` 五个菜单
- [x] 隐藏首页；登录后进视频管理
- [x] mock：`admin` / `admin`
- [x] `/ui/` 基路径、浅色、开发代理 Host+Origin
- [x] fake-server 保持官方启用

### Task 3: 构建、托管回归与冒烟 — 完成

- [x] `pnpm test` / `typecheck` / `build`
- [x] `test_frontend_hosting` 通过
- [x] `/` → `/ui/`，`/legacy/` 可用；控制台优先托管 `frontend_thin/dist`
- [x] 文档与设计状态已更新

**说明：** 旧目录 `frontend/`、`frontend_old_145806/` 可在关闭占用进程后手动删除；勿再改旧目录，以后只维护 `frontend_thin/`。
