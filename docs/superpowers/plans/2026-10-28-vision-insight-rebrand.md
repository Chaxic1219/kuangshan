# 视联洞察品牌与前端模块改名 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将活动前端与旧控制台品牌改为「视联洞察 / VISION INSIGHT」，并把 `frontend_thin` 业务模块 `mine` 迁到 `vision`。

**Architecture:** 仅改 `frontend_thin`、旧控制台文案与入口 README；保持 Hash 路由 path 与后端 API/`KUANGSHAN_*` 不变；历史前端副本不动。

**Tech Stack:** Vue 3、Pinia、Vite、Pure Admin thin、Python 控制台静态托管

**规格：** `docs/superpowers/specs/2026-10-28-vision-insight-rebrand-design.md`

---

### Task 1: 移动 frontend_thin 业务目录与核心文件

**Files:**
- Rename: `src/views/mine` → `src/views/vision`
- Rename: `src/components/mine` → `src/components/vision`
- Rename: `src/api/mine.ts` → `src/api/vision.ts`
- Rename: `src/stores/mine.ts` → `src/stores/vision.ts`
- Rename: `src/router/modules/mine.ts` → `src/router/modules/vision.ts`
- Rename: `src/style/mine.scss` → `src/style/vision.scss`

- [x] **Step 1:** 用 `git mv`（或等价）完成上述重命名
- [x] **Step 2:** 确认旧 `mine` 路径下无残留业务文件

### Task 2: 更新符号、import 与 CSS 前缀

**Files:**
- Modify: 所有引用 `@/stores/mine`、`@/api/mine`、`@/components/mine`、`@/views/mine`、`router/modules/mine`、`style/mine` 的文件
- Modify: `useMineStore` → `useVisionStore`；store id `mine-console` → `vision-console`
- Modify: 路由 name `Mine*` → `Vision*`
- Modify: CSS 类 `.mine-*` → `.vision-*`；变量 `--mine-*` → `--vision-*`
- Modify: 局部变量名 `mine` store 实例可改为 `vision`（可读性）

- [x] **Step 1:** 全库替换 import/符号/CSS 前缀
- [x] **Step 2:** `rg` 确认 `frontend_thin` 业务路径无 `views/mine`、`stores/mine`、`components/mine`

### Task 3: 品牌文案

**Files:**
- Modify: `frontend_thin/index.html`
- Modify: `frontend_thin/public/platform-config.json`
- Modify: `frontend_thin/package.json`
- Modify: `frontend_thin/src/views/login/index.vue`
- Modify: `frontend_thin/UPSTREAM.md`
- Modify: `frontend_thin/mock/asyncRoutes.ts` 等注释
- Modify: `analysis-service/console.html`
- Modify: 根 `README.md`、`analysis-service/README.md`（产品名表述）
- Modify: 启动脚本 description 中的「矿山」用户可见说明（若有）

- [x] **Step 1:** 替换用户可见「矿山视界 / MINE VISION / 矿山」为「视联洞察 / VISION INSIGHT」
- [x] **Step 2:** `rg` 确认 `frontend_thin` 与 `console.html` 无用户可见矿山品牌用语

### Task 4: 验证与构建

- [x] **Step 1:** `cd frontend_thin && pnpm typecheck`（直接跑 tsc/vue-tsc，通过）
- [x] **Step 2:** 运行相关前端测试（`node --test`，14 通过）
- [x] **Step 3:** `vite build` 更新 `frontend_thin/dist`（标题为视联洞察）
- [x] **Step 4:** 目视抽查登录标题与五页路由 import 无断链

### Task 5: 提交（仅当主人要求时）

- [ ] 只 stage 本改名相关文件，不含 `cameras.json`、历史副本、无关 diff

