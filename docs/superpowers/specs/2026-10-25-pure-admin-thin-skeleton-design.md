# Pure Admin 精简版完整骨架接入设计

日期：2026-10-25  
状态：已实施（活动工程目录 `frontend_thin/`）  
基线分支：`pure-admin-upgrade`  
上游：`pure-admin/pure-admin-thin`（非国际化精简版）

## 1. 目标

用官方 **pure-admin-thin** 的完整系统骨架（登录、侧栏、多标签页、主题、布局）作为外壳，在其中只挂载矿山视界既有五类业务能力，替代当前被过度裁剪的前端壳。

成功标准：

- 打开 `/ui/` 先进入原版登录页；默认账号密码 `admin` / `admin` 可进入。
- 登录后侧栏与标签页仅出现五个业务入口：视频管理、实时监控、告警大屏、场景模型、事件记录。
- 业务读写仍走本机 Python `analysis-service` 既有接口与 `X-Console-Token`；分析进程不因换页、刷新而重启。
- 旧版控制台保留在 `/legacy/`；有构建产物时 `/` 进入 `/ui/`。
- 默认浅色白底，与近期视觉要求一致。

## 2. 明确不做

- 不引入真实多用户权限平台或服务端账号体系；模板登录仅为本地演示壳。
- 不换成 `vue-pure-admin` 完整版。
- 不删除或重写 YOLO / MediaMTX / 告警 outbox / 摄像头设备关联逻辑。
- 不把模板 mock 登录当成后端安全边界；写接口仍校验本机 Host/Origin/令牌。
- 不保留模板欢迎页、权限演示页作为正式菜单（可保留源码文件但不进侧栏）。

## 3. 与现状差异

当前 `frontend/` 已基于 thin 改造，但路由器被改成无登录直达业务页，布局/导航也被局部替换，体验不像“原开源系统 + 业务标签页”。

本次采用 **干净重装（方案 A）**：

1. 备份现有矿山业务代码（`src/views/mine`、`src/components/mine`、`src/api/mine*`、`src/stores/mine.ts`、相关测试与托管适配文档）。
2. 重新取得固定提交的官方 thin 源码覆盖骨架（登录、mock、路由模块、布局保持原版）。
3. 把业务代码迁回，以**标准路由模块**注册五个菜单，而不是替换整个 `router/index.ts` 为自定义精简路由。
4. 恢复模板登录与 fake-server；固定 `admin` / `admin`。
5. 保留 Python `frontend_hosting.py`、`/ui/` 基路径、开发代理对 Host/Origin 的改写。

## 4. 信息架构

登录后默认进入「视频管理」。侧栏顺序：

1. 视频管理 `/cameras`
2. 实时监控 `/monitor`
3. 告警大屏 `/dashboard`
4. 场景模型 `/models`
5. 事件记录 `/alerts`

多标签页行为完全沿用 Pure Admin：点击菜单打开标签，可关闭、切换，不触发分析启停。

顶栏保留模板用户区与退出登录；可保留指向 `/legacy/` 的次要链接，但不替代主流程。

## 5. 登录与账号

- 使用模板自带登录页与 `vite-plugin-fake-server` mock。
- 默认账号：`admin`，默认密码：`admin`。
- 仅 `admin` 角色进入全部五个业务菜单；不额外做按钮级权限细分。
- 登录态与分析服务令牌分离：页面登录成功后，业务 store 仍通过 `GET /api/state` 获取控制台令牌；写操作失败/403 时提示重试，不自动重放危险写请求。

## 6. 工程与部署

| 项 | 约定 |
|---|---|
| 上游 | 记录提交号于 `frontend/UPSTREAM.md`，不跟踪浮动 main |
| 基路径 | `VITE_PUBLIC_PATH=/ui/`，Hash 路由 |
| 构建 | `pnpm build` → `frontend/dist/`，Python 同源托管 |
| 开发 | Vite 仅绑定 `127.0.0.1`；代理 `/api` 等并改写 Host、Origin |
| 主题 | 默认 light / 浅色；业务页不再使用独立深蓝大屏底 |
| 旧入口 | `/legacy/` 原生控制台回退 |

## 7. 业务复用边界

直接复用（迁入新骨架）：

- 摄像头四步编辑器、区域编辑、检测框组件
- 告警大屏聚合页、事件分页、筛选与导出
- `mine` 请求客户端与单一状态轮询

骨架侧恢复原版：

- `src/layout/**`、登录页、permission store、多标签、主题切换
- `mock/login.ts` 等 fake 路由
- 官方 `router/modules` 组织方式（新增 `mine.ts` 一类模块，隐藏/移除 home 演示菜单）

## 8. 验收要点

- 未登录访问业务路径会回到登录页；`admin`/`admin` 可进入。
- 五个菜单均可打开为标签页；刷新后登录态与菜单仍符合模板行为。
- `/api/state`、摄像头保存/启停、告警大屏与导出走真实后端（隔离数据目录做写测）。
- `pnpm test`、`pnpm typecheck`、`pnpm build` 通过；`test_frontend_hosting` 通过。
- 缺 `dist` 时 `/` 仍可回退旧版并提示；有 `dist` 时 `/` → `/ui/`。

## 9. 风险与回退

| 风险 | 处理 |
|---|---|
| 重装覆盖业务文件 | 先备份 `mine` 目录与 API/store，再覆盖骨架 |
| 登录壳与本机令牌两套身份混淆 | 文档与界面区分；写接口只认控制台令牌 |
| 本地曾缓存深色主题 | 更换或清空 `ResponsiveStorageNameSpace` |
| 重装后代理/基路径丢失 | 清单核对 `.env*`、`vite.config.ts`、托管测试 |

回退：恢复上一版 `frontend/` 备份或 git 还原；Python 托管与摄像头数据不受影响。

## 10. 结论

采用官方 pure-admin-thin **完整骨架重装**，登录默认 `admin`/`admin`，侧栏仅五个矿山业务标签页，业务实现复用现有 mine 模块，后端与旧入口策略不变。
