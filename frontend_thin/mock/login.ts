import { defineFakeRoute } from "vite-plugin-fake-server/client";

/**
 * 登录 / 改密 / 刷新一律走 Vite proxy → analysis-service（8765）。
 * 勿在此再 mock /api/auth/*：生产曾 enableProd 时假改密会“成功”但口令不落盘，
 * 重新登录仍只能用默认 Admin123，表现为“改完又变回默认密码”。
 */
export default defineFakeRoute([]);
