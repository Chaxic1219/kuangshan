# 视联洞察品牌与前端模块改名设计

日期：2026-10-28  
状态：已实施（2026-10-28）  
基线：活动前端 `frontend_thin/`；旧控制台 `/legacy/`；环境变量仍为 `KUANGSHAN_*`  
已确认：方案 1 — 只动活动前端 + 旧控制台文案 + 入口文档；不做全量环境变量/仓库改名。

## 1. 目标与边界

将产品对外名称从「矿山视界 / MINE VISION」改为视觉向品牌 **视联洞察 / VISION INSIGHT**，并在活动前端中把业务模块路径与符号从 `mine` 迁到 `vision`，避免代码与界面继续暴露矿山语义。

成功标准：

- `/ui/` 与 `/legacy/` 用户可见文案不再出现「矿山」「MINE VISION」「mine vision」等矿山相关品牌用语。
- `frontend_thin` 业务目录、路由模块、store、组件与 API 客户端统一使用 `vision` 命名。
- 分析进程、摄像头配置、告警 outbox、`KUANGSHAN_*` 环境变量行为不变；换页/刷新不触发分析重启。
- `pnpm typecheck`、相关前端测试通过，并重新构建 `frontend_thin/dist` 供控制台托管。

## 2. 非目标

- 不重命名仓库目录、GitHub 仓库名、`analysis-service` 数据目录。
- 不迁移环境变量 `KUANGSHAN_*`、单实例锁名/锁路径（如 `.kuangshan`）。
- 不批量改写 `frontend/`、`frontend_old_*`、`_mine_backup/` 等历史副本。
- 不改业务功能、API 契约、检测场景或模型逻辑。
- 不在本期做 Linux 二期或跨平台补齐。

## 3. 品牌约定

| 用途 | 新值 |
|---|---|
| 中文产品名 | 视联洞察 |
| 英文副标 | VISION INSIGHT |
| `package.json` name | `vision-insight-console` |
| 本地存储命名空间 | `vision-insight-thin-` |
| 页面标题模式 | `{页面} · 视联洞察` 或 `视联洞察 · 视频智能分析` |

## 4. 改动范围

### 4.1 `frontend_thin` 用户可见文案

- `index.html` title
- `public/platform-config.json` 的 `Title`、`ResponsiveStorageNameSpace`
- 登录页品牌 alt / 文案
- 侧栏或布局中若仍有「矿山」字样一并替换
- 注释与 `UPSTREAM.md` 中「矿山业务」改为「视联洞察业务」或「视觉业务」

### 4.2 `frontend_thin` 模块路径与符号

| 旧 | 新 |
|---|---|
| `src/views/mine/` | `src/views/vision/` |
| `src/components/mine/` | `src/components/vision/` |
| `src/api/mine.ts`（及同类 mine API） | `src/api/vision.ts`（保持导出能力等价） |
| `src/stores/mine.ts` | `src/stores/vision.ts` |
| `src/router/modules/mine.ts` | `src/router/modules/vision.ts` |
| `useMineStore` / store id `mine-console` | `useVisionStore` / `vision-console` |
| 路由 name `MineCameras` 等 | `VisionCameras` 等 |

所有 import、动态 import、`main.ts` 启动时对 store 的引用同步更新。Hash 路由 path（如 `/cameras`）保持不变，避免书签与文档失效。

### 4.3 旧控制台与入口文档

- `analysis-service/console.html`：title、品牌区「矿山视界 / MINE VISION」→「视联洞察 / VISION INSIGHT」
- 根 `README.md`、`analysis-service/README.md`：产品标题与「矿山业务菜单」等表述改为视联洞察 / 视觉业务
- 启动脚本描述字符串中若出现「矿山分析控制台」等用户可见说明，改为「视联洞察分析控制台」（不改脚本文件名）

### 4.4 明确不改

- `KUANGSHAN_MAX_RUNNING`、`KUANGSHAN_ALERT_*`、`KUANGSHAN_MEDIA_ROOT`、`KUANGSHAN_LOCK_DIR` 等
- `data/cameras.json`、权重、证据、outbox
- `frontend/`、`frontend_old_145806/`、`_mine_backup/`
- 仓库路径 `kuangshan new`、远程 `kuangshan.git`

## 5. 实施顺序

1. 在 `frontend_thin` 内移动/重命名目录与文件，批量修正 import 与符号。
2. 替换品牌文案与 `package.json` / `platform-config.json`。
3. 更新旧控制台 `console.html` 与入口 README。
4. 运行 `pnpm typecheck`、相关测试、`pnpm build`。
5. 目视检查 `/ui/` 登录与五页侧栏、`/legacy/` 品牌区。

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 漏改动态 import 导致白屏 | typecheck + 构建后打开五页冒烟 |
| Pinia store id 变更导致旧 localStorage 残留 | 新命名空间可接受；不迁移旧 mine 缓存 |
| 历史副本仍含「矿山」 | 文档注明仅活动前端与旧控制台入口；副本不在本期范围 |
| 大范围 rename 与无关工作区 diff 混杂 | 提交时只纳入本改名相关文件 |

## 7. 验收清单

1. 搜索活动前端与 `console.html`：用户可见字符串无「矿山」「MINE VISION」。
2. `frontend_thin` 下无业务路径 `views/mine`、`components/mine`、`stores/mine`、`router/modules/mine`。
3. `pnpm typecheck`、约定测试、`pnpm build` 通过；`frontend_thin/dist` 更新。
4. 打开 `/ui/`：标题与登录为视联洞察；五页可进；写接口仍走既有 `/api/*` 与令牌。
5. 打开 `/legacy/`：品牌为视联洞察；分析启停不受影响。

## 8. 待主人审阅

请确认本规格无歧义后，再进入实施计划与改码。
