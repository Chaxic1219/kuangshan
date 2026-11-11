# 上游与本地适配

- 来源：https://github.com/pure-admin/pure-admin-thin
- 取得日期：2026-10-25
- 固定提交：`f0ff132561ab684bb78379239adf29f9a38ac7f1`
- 上游版本：6.2.0，非国际化精简版，MIT（保留 LICENSE）。
- 本地验证环境：Node 24.x、pnpm 10.x。

保留 Pure Admin 完整骨架：登录页、侧栏、多标签、主题、fake-server。  
视觉业务位于 `src/views/vision`、`src/components/vision`、`src/api/vision.ts`、`src/stores/vision.ts`、`src/router/modules/vision.ts`。

- 演示登录：`admin` / `admin`（仅前端 mock，不是分析服务鉴权）
- 构建产物目录：优先 `frontend_thin/dist`（当前活动工程）；由 Python 同源托管 `/ui/`
- 旧界面：`/legacy/`

更新上游前先审查差异，勿覆盖上述业务适配。
