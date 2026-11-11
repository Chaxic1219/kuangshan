/** 业务写操作走登录 Bearer；不再依赖 /api/state 下发的 console token。 */

function readAccessToken() {
  try {
    const match = document.cookie.match(/(?:^|;\s*)authorized-token=([^;]*)/);
    if (match) {
      const raw = decodeURIComponent(match[1]);
      const data = JSON.parse(raw);
      if (data?.accessToken) return String(data.accessToken);
    }
  } catch {
    /* ignore */
  }
  try {
    const cached = JSON.parse(localStorage.getItem("user-info") || "{}");
    if (cached?.accessToken) return String(cached.accessToken);
  } catch {
    /* ignore */
  }
  return "";
}

export function createClient(fetcher = globalThis.fetch) {
  return {
    async request(path, data, signal) {
      if (!path.startsWith("/api/") || path.includes("\\"))
        throw new Error("请求路径无效");
      const write = data !== undefined;
      const accessToken = readAccessToken();
      if (write && !accessToken) throw new Error("请先登录后再操作");
      const timeout = AbortSignal.timeout(write ? 90000 : 30000);
      const headers = {};
      if (write) {
        headers["Content-Type"] = "application/json";
        headers.Authorization = `Bearer ${accessToken}`;
      } else if (accessToken) {
        headers.Authorization = `Bearer ${accessToken}`;
      }
      const response = await fetcher(path, {
        method: write ? "POST" : "GET",
        headers,
        body: write ? JSON.stringify(data) : undefined,
        cache: "no-store",
        signal: signal ? AbortSignal.any([signal, timeout]) : timeout
      });
      let result;
      try {
        result = await response.json();
      } catch {
        throw new Error("服务返回格式异常，请检查后台连接");
      }
      if (!response.ok)
        throw new Error(result.error || result.message || `请求失败（${response.status}）`);
      return result;
    }
  };
}
