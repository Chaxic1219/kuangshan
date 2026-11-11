import { defineStore } from 'pinia';
import { ref } from 'vue';
import { request } from '@/api/vision';

export const useVisionStore = defineStore('vision-console', () => {
  const state = ref<any>({ cameras: [], models: [], events: {}, alerts: [], capacity: {} });
  const connected = ref(false);
  const error = ref('');
  const updatedAt = ref('');
  let active = false;
  let pending: Promise<void> | undefined;
  let controller: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout>;
  /** 默认 1.5s；放大页叠框可临时加快（后端 status 约 0.1s 写一次）。 */
  let pollMs = 1500;

  function refresh(): Promise<void> {
    if (pending) return pending;
    controller = new AbortController();
    pending = request('/api/state', undefined, controller.signal).then(next => {
      // 令牌留在请求层，不进入可持久化的业务状态。
      const { token: _token, ...snapshot } = next;
      state.value = snapshot;
      connected.value = true;
      error.value = '';
      updatedAt.value = new Date().toISOString();
    }).catch(e => {
      if (e.name !== 'AbortError') {
        connected.value = false;
        error.value = e.message || '服务连接失败';
      }
    }).finally(() => { pending = undefined; });
    return pending;
  }
  async function tick() {
    clearTimeout(timer);
    if (!active || document.hidden) return;
    await refresh();
    if (active && !document.hidden) timer = setTimeout(tick, pollMs);
  }
  function setPollInterval(ms: number) {
    const next = Math.max(200, Math.min(5000, Math.round(ms) || 1500));
    if (next === pollMs) return;
    pollMs = next;
    if (active && !document.hidden) {
      clearTimeout(timer);
      void tick();
    }
  }
  function visibility() {
    clearTimeout(timer);
    if (!document.hidden) void tick();
  }
  function start() {
    if (active) return;
    active = true;
    document.addEventListener('visibilitychange', visibility);
    void tick();
  }
  function stop() {
    active = false;
    clearTimeout(timer);
    controller?.abort();
    document.removeEventListener('visibilitychange', visibility);
  }
  return { state, connected, error, updatedAt, refresh, start, stop, setPollInterval };
});
