# Pure Admin 前端迁移实施计划

执行方式：使用 subagent-driven-development 按独立文件分工，主代理负责集成、规格审查和验证。

目标：完成五个业务页面的 Vue 3 迁移，保持现有接口及设备配置，构建后由 Python 同源提供服务。

架构：官方 Pure Admin 精简版作为基础；新增矿山业务页面和状态适配器；旧控制台作为回退入口。

技术：Vue 3、TypeScript、Element Plus、Pinia、Vite、Python unittest。

- [x] 基线：记录官方提交，获取模板；运行当前后端测试，不修改真实摄像头及流配置。
- [x] 托管：在 `analysis-service/tests/test_frontend_hosting.py` 先覆盖入口、缺失构建、MIME、路径越界及旧入口；实现受限静态资源托管并接入 `console.py`。
- [x] 前端基础：新增 `frontend/src/api/mine.ts` 适配现有接口，单一状态轮询、令牌、错误处理；纯函数单测覆盖身份字段和区域坐标。
- [x] 摄像头组件：新增 `CameraEditor.vue`、`RegionEditor.vue`、`DetectionFrame.vue`；四步配置、统一 USB/RTSP 接入、同帧渲染与区域绑定。
- [x] 页面：新增视频、监控、模型、告警大屏和事件页面；真实接口、筛选分页和导出；无模拟业务数据。
- [x] 构建：本地图标资源、Hash 路由、开发代理、类型检查、生产构建；无需运行 Node 服务。
- [x] 验收：后端 unittest（托管/告警相关）、前端测试及类型检查、构建产物 `/ui/` 可访问、旧入口 `/legacy/` 可回退。
- [x] 交付：更新使用文档；有构建产物时 `/` 进入 `/ui/`，保留 `/legacy/`；本机摄像头配置与临时脚本不纳入提交。

验证命令由 `_run_` Python 临时脚本封装执行；后端使用 `python -m unittest discover -s tests`，前端使用锁定包管理器的 `typecheck`、`test`、`build`。隔离服务使用独立临时数据目录与端口，写入测试绝不使用正式数据库。

## 验收记录（2026-10-25）

- 上游：`pure-admin-thin@f0ff132`（6.2.0），见 `frontend/UPSTREAM.md`
- 前端：`pnpm test`、`pnpm typecheck`、`pnpm build`
- 后端：`unittest discover -p test_frontend_hosting.py`、`test_alert_dashboard.py`
- 入口：`/` → `/ui/`（有 dist）；`/legacy/` 旧版；缺构建时 `/` 仍为旧版并提示使用 `/legacy/`
