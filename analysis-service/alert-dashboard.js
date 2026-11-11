'use strict';
/**
 * 告警数据大屏：筛选、三类 SVG 图、10 秒刷新、全屏与证据。
 * 由 console.js 挂载/卸载；不依赖外部图表库。
 */
const AlertDashboard = (() => {
  const PRESETS = [
    {id: 'today', label: '今日'},
    {id: 'last24h', label: '最近 24 小时'},
    {id: 'last7d', label: '近 7 天'},
    {id: 'last30d', label: '近 30 天'},
    {id: 'custom', label: '自定义'},
  ];
  const COLORS = ['#f0c36a', '#39d9bc', '#6ea8fe', '#ff7966', '#c9852c', '#8fd3a0', '#d4a5ff', '#7ec8e3'];
  const STATE_LABEL = {
    running: '分析中', streaming: '已取流', connecting: '连接中', starting: '启动中',
    reconnecting: '重连中', stopped: '未运行', error: '故障', backoff: '自动恢复中', failed: '已失败', deleted: '已删除',
  };

  let host = null;
  let opts = {};
  let filters = {preset: 'today', from: '', to: '', cameraId: '', eventId: ''};
  let data = null;
  let lastOkAt = null;
  let stale = false;
  let reqSeq = 0;
  let timer = null;
  let mounted = false;
  let detailPage = 1;
  let detailTotal = 0;
  let showDetail = false;

  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const qs = (sel, root) => (root || host).querySelector(sel);
  const qsa = (sel, root) => [...(root || host).querySelectorAll(sel)];

  function dateText(value) {
    if (!value) return '—';
    try {
      return new Date(value).toLocaleString('zh-CN', {hour12: false, timeZone: 'Asia/Shanghai'});
    } catch {
      return value;
    }
  }

  function localInputValue(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '';
    const pad = n => String(n).padStart(2, '0');
    // datetime-local 按本地墙钟；站点口径 UTC+8，与 Asia/Shanghai 同偏移。
    const local = new Date(d.getTime() + 8 * 3600 * 1000);
    return `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())}T${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}`;
  }

  function fromLocalInput(value) {
    if (!value) return '';
    // 解释为 UTC+8 墙钟 → ISO UTC
    const m = value.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/);
    if (!m) return value;
    const utc = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4] - 8, +m[5], 0);
    return new Date(utc).toISOString();
  }

  function queryString() {
    const p = new URLSearchParams();
    p.set('preset', filters.preset || 'today');
    if (filters.preset === 'custom') {
      if (filters.from) p.set('from', filters.from);
      if (filters.to) p.set('to', filters.to);
    }
    if (filters.cameraId) p.set('cameraId', filters.cameraId);
    if (filters.eventId) p.set('eventId', filters.eventId);
    return p.toString();
  }

  function mount(el, options) {
    unmount();
    host = el;
    opts = options || {};
    mounted = true;
    host.innerHTML = shellHtml();
    bindChrome();
    document.addEventListener('fullscreenchange', onFullscreenChange);
    document.addEventListener('visibilitychange', onVisibility);
    load(true);
    schedule();
  }

  function unmount() {
    clearTimeout(timer);
    timer = null;
    document.removeEventListener('fullscreenchange', onFullscreenChange);
    document.removeEventListener('visibilitychange', onVisibility);
    if (host && document.fullscreenElement === qs('.dash-shell')) {
      document.exitFullscreen?.().catch(() => {});
    }
    document.body.classList.remove('dash-fullscreen');
    if (host) host.innerHTML = '';
    host = null;
    mounted = false;
    data = null;
    showDetail = false;
  }

  function schedule() {
    clearTimeout(timer);
    if (!mounted) return;
    timer = setTimeout(() => {
      if (!mounted) return;
      if (document.hidden) {
        schedule();
        return;
      }
      load(false).finally(schedule);
    }, 10000);
  }

  function onVisibility() {
    if (!document.hidden && mounted) load(false);
  }

  function shellHtml() {
    return `<div class="dash-shell">
      <div class="dash-toolbar">
        <div class="filters">
          <label>时间范围<select data-f="preset">${PRESETS.map(p => `<option value="${p.id}">${p.label}</option>`).join('')}</select></label>
          <label class="custom-range" hidden>开始<input type="datetime-local" data-f="from"></label>
          <label class="custom-range" hidden>结束<input type="datetime-local" data-f="to"></label>
          <label>摄像头<select data-f="cameraId"><option value="">全部摄像头</option></select></label>
          <label>事件类型<select data-f="eventId"><option value="">全部类型</option></select></label>
        </div>
        <div class="meta">
          <span data-role="updated">尚未更新</span>
          <button type="button" data-act="refresh">刷新</button>
          <button type="button" data-act="export">导出 CSV</button>
          <button type="button" class="primary" data-act="fullscreen">全屏</button>
          <button type="button" data-act="exit-fs" hidden>退出全屏</button>
        </div>
      </div>
      <div data-role="body"><div class="dash-card"><p class="empty-note">正在加载告警统计…</p></div></div>
      <dialog id="dash-evidence">
        <header class="modalhead" style="display:flex;justify-content:space-between;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid #243f4a">
          <div><h2 style="margin:0;font-size:16px;color:#e8f3f6" data-ev-title>告警证据</h2><p class="small" data-ev-sub style="margin:4px 0 0;color:#8ea3ab"></p></div>
          <button type="button" class="close" data-ev-close aria-label="关闭">×</button>
        </header>
        <div class="modalcontent" style="padding:16px">
          <div data-ev-stage></div>
          <div data-ev-detail class="notice" style="margin-top:12px;color:#c5d7dd;font-size:13px"></div>
        </div>
      </dialog>
    </div>`;
  }

  function bindChrome() {
    const shell = qs('.dash-shell');
    qs('[data-f="preset"]').value = filters.preset;
    qsa('[data-f]').forEach(el => {
      el.onchange = () => {
        const key = el.dataset.f;
        if (key === 'from' || key === 'to') filters[key] = fromLocalInput(el.value);
        else filters[key] = el.value;
        if (key === 'preset') syncCustomVisibility();
        detailPage = 1;
        showDetail = false;
        load(true);
      };
    });
    qs('[data-act="refresh"]').onclick = () => load(true);
    qs('[data-act="export"]').onclick = () => {
      const a = document.createElement('a');
      a.href = '/api/alerts/export.csv?' + queryString();
      a.download = 'alerts.csv';
      document.body.append(a);
      a.click();
      a.remove();
      opts.toast?.('已按当前筛选导出 CSV');
    };
    qs('[data-act="fullscreen"]').onclick = enterFullscreen;
    qs('[data-act="exit-fs"]').onclick = () => document.exitFullscreen?.().catch(() => {});
    qs('[data-ev-close]').onclick = () => qs('#dash-evidence').close();
    shell.onclick = e => {
      const el = e.target.closest('[data-filter-event],[data-filter-camera],[data-kpi],[data-alert-id],[data-clear-filter],[data-goto],[data-page-detail],[data-detail-nav]');
      if (!el) return;
      e.stopPropagation();
      if (el.dataset.clearFilter != null) {
        filters.cameraId = '';
        filters.eventId = '';
        syncSelects();
        load(true);
        return;
      }
      if (el.dataset.filterEvent != null) {
        filters.eventId = el.dataset.filterEvent;
        syncSelects();
        load(true);
        return;
      }
      if (el.dataset.filterCamera != null) {
        filters.cameraId = el.dataset.filterCamera;
        syncSelects();
        load(true);
        return;
      }
      if (el.dataset.kpi === 'types') {
        qs('.dash-card[data-panel="types"]')?.scrollIntoView({behavior: 'smooth', block: 'nearest'});
        return;
      }
      if (el.dataset.kpi === 'cameras') {
        qs('.dash-card[data-panel="cameras"]')?.scrollIntoView({behavior: 'smooth', block: 'nearest'});
        return;
      }
      if (el.dataset.kpi === 'top' && data?.summary?.topType) {
        filters.eventId = data.summary.topType.eventId;
        syncSelects();
        load(true);
        return;
      }
      if (el.dataset.kpi === 'latest' && data?.recent?.[0]) {
        openEvidence(data.recent[0]);
        return;
      }
      if (el.dataset.kpi === 'total') {
        showDetail = true;
        detailPage = 1;
        loadDetail();
        return;
      }
      if (el.dataset.alertId) {
        const row = (data?.recent || []).find(r => r.id === el.dataset.alertId)
          || (host._detailRows || []).find(r => r.id === el.dataset.alertId);
        if (row) openEvidence(row);
        return;
      }
      if (el.dataset.goto === 'cameras' || el.dataset.goto === 'monitor') {
        opts.gotoPage?.(el.dataset.goto);
        return;
      }
      if (el.dataset.pageDetail != null) {
        showDetail = true;
        detailPage = 1;
        loadDetail();
        return;
      }
      if (el.dataset.detailNav) {
        const next = detailPage + Number(el.dataset.detailNav);
        if (next < 1) return;
        detailPage = next;
        loadDetail();
      }
    };
    syncCustomVisibility();
  }

  function syncCustomVisibility() {
    const custom = filters.preset === 'custom';
    qsa('.custom-range').forEach(el => { el.hidden = !custom; });
  }

  function syncSelects() {
    const cam = qs('[data-f="cameraId"]');
    const ev = qs('[data-f="eventId"]');
    if (cam) cam.value = filters.cameraId || '';
    if (ev) ev.value = filters.eventId || '';
  }

  async function load(force) {
    if (!mounted) return;
    const seq = ++reqSeq;
    try {
      const payload = await opts.api('/api/alerts/dashboard?' + queryString());
      if (seq !== reqSeq || !mounted) return;
      data = payload;
      lastOkAt = payload.generatedAt;
      stale = false;
      fillFilterOptions(payload);
      renderBody();
      if (showDetail) loadDetail();
    } catch (err) {
      if (seq !== reqSeq || !mounted) return;
      stale = true;
      if (!data) {
        qs('[data-role="body"]').innerHTML = `<div class="dash-card"><p class="empty-note">统计加载失败：${esc(err.message)} <button type="button" data-act="retry">重试</button></p></div>`;
        qs('[data-act="retry"]')?.addEventListener('click', () => load(true));
      } else {
        updateMeta();
        opts.toast?.(err.message || '数据未更新', true);
      }
    }
  }

  function fillFilterOptions(payload) {
    const cam = qs('[data-f="cameraId"]');
    const ev = qs('[data-f="eventId"]');
    if (!cam || !ev) return;
    const camVal = filters.cameraId;
    const evVal = filters.eventId;
    cam.innerHTML = '<option value="">全部摄像头</option>' + (payload.filterCameras || []).map(c => {
      const tag = c.status === 'deleted' ? '（已删除）' : (c.state === 'running' ? '（分析中）' : '');
      return `<option value="${esc(c.cameraId)}">${esc(c.cameraName || c.cameraId)}${tag}</option>`;
    }).join('');
    ev.innerHTML = '<option value="">全部类型</option>' + (payload.filterEvents || []).map(e =>
      `<option value="${esc(e.eventId)}">${esc(e.eventName)}</option>`).join('');
    cam.value = camVal || '';
    ev.value = evVal || '';
    if (filters.preset === 'custom') {
      const fromEl = qs('[data-f="from"]');
      const toEl = qs('[data-f="to"]');
      if (fromEl && !fromEl.value) fromEl.value = localInputValue(payload.range?.from);
      if (toEl && !toEl.value) toEl.value = localInputValue(payload.range?.to);
    }
  }

  function updateMeta() {
    const el = qs('[data-role="updated"]');
    if (!el) return;
    const range = data?.range;
    const rangeText = range ? `${dateText(range.from)} ~ ${dateText(range.to)}` : '';
    el.textContent = stale
      ? `数据未更新 · 上次成功 ${dateText(lastOkAt)}`
      : `已更新 ${dateText(data?.generatedAt)} · ${data?.timezone || 'UTC+8'} · ${rangeText}`;
    el.classList.toggle('warn', stale);
  }

  function renderBody() {
    if (!data) return;
    updateMeta();
    const s = data.summary || {};
    const top = s.topType;
    const topLabel = top
      ? (top.tied ? `${top.eventName}（并列）` : top.eventName)
      : '—';
    const activeFilter = filters.cameraId || filters.eventId;
    const body = qs('[data-role="body"]');
    body.innerHTML = `
      <div class="dash-kpis">
        <div class="dash-kpi" data-kpi="total" title="查看明细"><span>告警事件总数</span><strong>${s.totalEvents ?? 0}</strong><small>点击查看明细</small></div>
        <div class="dash-kpi" data-kpi="cameras"><span>发生告警路数</span><strong>${s.cameraCount ?? 0}</strong><small>有告警的摄像头</small></div>
        <div class="dash-kpi" data-kpi="types"><span>告警类型数</span><strong>${s.typeCount ?? 0}</strong><small>非零事件类型</small></div>
        <div class="dash-kpi" data-kpi="top"><span>最多告警类型</span><strong style="font-size:18px">${esc(topLabel)}</strong><small>${top ? top.count + ' 次' + (top.tied ? ' · ' + (top.tiedNames || []).join('、') : '') : '暂无'}</small></div>
        <div class="dash-kpi" data-kpi="latest"><span>最近告警时间</span><strong style="font-size:16px">${esc(dateText(s.latestAt))}</strong><small>点击查看证据</small></div>
      </div>
      ${activeFilter ? `<div class="dash-foot"><span>当前筛选：${filters.cameraId ? '摄像头 ' + esc(filters.cameraId) : ''}${filters.cameraId && filters.eventId ? ' · ' : ''}${filters.eventId ? '类型 ' + esc((data.byType || []).find(t => t.eventId === filters.eventId)?.eventName || filters.eventId) : ''} <button type="button" data-clear-filter>清除筛选</button></span></div>` : ''}
      ${(data.hints || []).map(h => `<div class="dash-foot"><span class="warn">${esc(h)}</span></div>`).join('')}
      ${data.excludedRecords ? `<div class="dash-foot"><span class="warn">部分记录未纳入统计（${data.excludedRecords} 条字段无效或损坏）</span></div>` : ''}
      <div class="dash-grid">
        <div class="dash-card" data-panel="trend"><h3>告警数量趋势（按${data.bucket === 'hour' ? '小时' : '天'}）</h3>${trendSvg(data.trend || [])}</div>
        <div class="dash-card" data-panel="types"><h3>告警类型分布</h3>${typeChart(data.byType || [], s.totalEvents || 0)}</div>
      </div>
      <div class="dash-grid secondary">
        <div class="dash-card" data-panel="cameras"><h3>摄像头告警排行</h3>${cameraBars(data.byCamera || [])}</div>
        <div class="dash-card"><h3>最近告警 <button type="button" data-page-detail style="float:right;font-size:12px">分页明细</button></h3>${recentTable(data.recent || [])}</div>
      </div>
      <div class="dash-card" style="min-height:auto">
        <h3>当前分析状态</h3>
        <div class="dash-status">${analysisChips(data.analysis || [])}</div>
        <div class="dash-foot" style="margin-top:12px">
          <span>分析状态取自当前运行配置，不受历史时间筛选影响。</span>
          <span><button type="button" data-goto="monitor">实时监控</button> <button type="button" data-goto="cameras">视频管理</button></span>
        </div>
      </div>
      <div data-role="detail" ${showDetail ? '' : 'hidden'}></div>
      <div class="dash-foot"><span>口径：一条本地告警记录计 1 次事件；告警减少不等于安全改善。</span></div>`;
  }

  function trendSvg(points) {
    if (!points.length) return '<p class="empty-note">当前范围暂无告警</p>';
    const w = 640, h = 200, pad = 28;
    const max = Math.max(1, ...points.map(p => p.count));
    const step = points.length > 1 ? (w - pad * 2) / (points.length - 1) : 0;
    const coords = points.map((p, i) => {
      const x = pad + i * step;
      const y = h - pad - (p.count / max) * (h - pad * 2);
      return {x, y, ...p};
    });
    const line = coords.map((c, i) => `${i ? 'L' : 'M'}${c.x},${c.y}`).join(' ');
    const area = `${line} L${coords.at(-1).x},${h - pad} L${coords[0].x},${h - pad} Z`;
    const labels = coords.filter((_, i) => i === 0 || i === coords.length - 1 || i % Math.ceil(coords.length / 6) === 0);
    return `<svg class="dash-chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="告警趋势">
      <path d="${area}" fill="#f0c36a22"/>
      <path d="${line}" fill="none" stroke="#f0c36a" stroke-width="2"/>
      ${coords.map(c => `<circle cx="${c.x}" cy="${c.y}" r="3" fill="#f0c36a"><title>${esc(c.bucket)}：${c.count}</title></circle>`).join('')}
      ${labels.map(c => `<text x="${c.x}" y="${h - 8}" fill="#8ea3ab" font-size="10" text-anchor="middle">${esc(c.bucket.slice(-5))}</text>`).join('')}
      <text x="${pad}" y="16" fill="#8ea3ab" font-size="11">峰值 ${max}</text>
    </svg>
    <div class="dash-legend">${coords.slice(-8).map(c => `<span>${esc(c.bucket)} · ${c.count}</span>`).join(' · ')}</div>`;
  }

  function typeChart(items, total) {
    if (!items.length) return '<p class="empty-note">当前范围暂无告警</p>';
    const r = 70, cx = 90, cy = 100;
    let angle = -Math.PI / 2;
    const arcs = items.map((item, i) => {
      const slice = total ? (item.count / total) * Math.PI * 2 : 0;
      const a0 = angle;
      angle += slice;
      const a1 = angle;
      const large = slice > Math.PI ? 1 : 0;
      const x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
      const x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
      const d = slice === 0 ? '' : (items.length === 1
        ? `M ${cx} ${cy - r} A ${r} ${r} 0 1 1 ${cx - 0.01} ${cy - r} Z`
        : `M ${cx} ${cy} L ${x0} ${y0} A ${r} ${r} 0 ${large} 1 ${x1} ${y1} Z`);
      return {item, color: COLORS[i % COLORS.length], d};
    });
    return `<div style="display:flex;gap:16px;align-items:flex-start;flex-wrap:wrap">
      <svg class="dash-chart" style="width:180px;height:200px" viewBox="0 0 180 200" role="img" aria-label="类型分布">
        ${arcs.map(a => a.d ? `<path d="${a.d}" fill="${a.color}" stroke="#12262f" stroke-width="1"/>` : '').join('')}
        <circle cx="${cx}" cy="${cy}" r="38" fill="#12262f"/>
        <text x="${cx}" y="${cy + 4}" text-anchor="middle" fill="#e8f3f6" font-size="16">${total}</text>
      </svg>
      <div class="dash-legend" style="flex:1">${items.map((item, i) =>
        `<button type="button" data-filter-event="${esc(item.eventId)}"><span class="bar" style="background:${COLORS[i % COLORS.length]}"></span>${esc(item.eventName)} · ${item.count}（${Math.round((item.share || 0) * 100)}%）</button>`
      ).join('')}</div>
    </div>`;
  }

  function cameraBars(items) {
    if (!items.length) return '<p class="empty-note">当前范围暂无告警</p>';
    const max = Math.max(1, ...items.map(i => i.count));
    return `<div class="dash-legend">${items.map(item => {
      const name = item.cameraName || item.cameraId || '未知';
      const width = Math.max(4, Math.round(item.count / max * 100));
      const filter = item.remainder ? '' : `data-filter-camera="${esc(item.cameraId)}"`;
      return `<button type="button" ${filter} ${item.remainder ? 'disabled style="opacity:.85;cursor:default"' : ''}>
        <div style="display:flex;justify-content:space-between;gap:8px;margin-bottom:4px"><span>${esc(name)}</span><span>${item.count}</span></div>
        <div style="height:8px;background:#18323c;border-radius:4px;overflow:hidden"><div style="width:${width}%;height:100%;background:#39d9bc"></div></div>
      </button>`;
    }).join('')}</div>`;
  }

  function recentTable(rows) {
    if (!rows.length) return '<p class="empty-note">当前范围暂无告警</p>';
    return `<table class="dash-recent"><thead><tr><th>证据</th><th>时间</th><th>类型</th><th>摄像头</th><th></th></tr></thead><tbody>
      ${rows.map(r => `<tr>
        <td>${r.image ? `<img src="${esc(r.image)}" alt="证据" data-alert-id="${esc(r.id)}" onerror="this.style.display='none';this.insertAdjacentHTML('afterend','<span class=small>证据图片暂不可用</span>')">` : '<span class="small">证据图片暂不可用</span>'}</td>
        <td>${esc(dateText(r.at || r.capturedAt))}</td>
        <td>${esc(r.eventName)}</td>
        <td>${esc(r.cameraName || r.cameraId)}</td>
        <td><button type="button" data-alert-id="${esc(r.id)}">查看证据</button></td>
      </tr>`).join('')}
    </tbody></table>`;
  }

  function analysisChips(rows) {
    if (!rows.length) return '<span class="chip">当前没有已配置摄像头</span>';
    return rows.map(r => {
      const state = r.state || (r.running ? 'running' : 'stopped');
      const cls = state === 'running' ? 'good' : (state === 'failed' || state === 'error' ? 'bad' : (state === 'reconnecting' || state === 'backoff' ? 'wait' : ''));
      return `<span class="chip ${cls}">${esc(r.cameraName || r.cameraId)} · ${esc(STATE_LABEL[state] || state)}</span>`;
    }).join('');
  }

  async function loadDetail() {
    const box = qs('[data-role="detail"]');
    if (!box) return;
    box.hidden = false;
    try {
      const p = new URLSearchParams(queryString());
      p.set('page', String(detailPage));
      p.set('pageSize', '20');
      const payload = await opts.api('/api/alerts/query?' + p.toString());
      detailTotal = payload.total || 0;
      host._detailRows = payload.rows || [];
      const pages = Math.max(1, Math.ceil(detailTotal / (payload.pageSize || 20)));
      box.innerHTML = `<div class="dash-card"><h3>告警明细（${detailTotal}）</h3>
        ${recentTable(payload.rows || [])}
        <div class="dash-foot" style="margin-top:12px">
          <span>第 ${payload.page}/${pages} 页</span>
          <span>
            <button type="button" data-detail-nav="-1" ${detailPage <= 1 ? 'disabled' : ''}>上一页</button>
            <button type="button" data-detail-nav="1" ${detailPage >= pages ? 'disabled' : ''}>下一页</button>
          </span>
        </div></div>`;
    } catch (err) {
      box.innerHTML = `<div class="dash-card"><p class="empty-note">明细加载失败：${esc(err.message)}</p></div>`;
    }
  }

  function openEvidence(row) {
    if (!row) return;
    const alert = {
      id: row.id,
      cameraName: row.cameraName || row.cameraId,
      cameraId: row.cameraId,
      eventId: row.eventId,
      eventName: row.eventName,
      image: row.image,
      detections: row.detections || [],
      regions: row.regions || [],
      capturedAt: row.capturedAt || row.at,
      alarm: row.alarm !== false,
      inferenceMs: row.inferenceMs,
    };
    const fs = document.fullscreenElement;
    if (fs && qs('.dash-shell') === fs) {
      const dlg = qs('#dash-evidence');
      qs('[data-ev-title]').textContent = alert.cameraName || '告警证据';
      qs('[data-ev-sub]').textContent = `${alert.eventName || ''} · ${dateText(alert.capturedAt)}`;
      const stage = qs('[data-ev-stage]');
      if (opts.renderEvidence) stage.innerHTML = opts.renderEvidence(alert);
      else {
        stage.innerHTML = alert.image
          ? `<img src="${esc(alert.image)}" alt="证据" style="max-width:100%;border-radius:8px" onerror="this.alt='证据图片暂不可用'">`
          : '<p class="empty-note">证据图片暂不可用</p>';
      }
      const dets = (alert.detections || []).map(d => d.label).join('、') || '无目标';
      qs('[data-ev-detail]').textContent = `${alert.eventName} · ${dets}`;
      if (!dlg.open) dlg.showModal();
      return;
    }
    opts.openEvidence?.(alert);
  }

  async function enterFullscreen() {
    const shell = qs('.dash-shell');
    if (!shell) return;
    if (!shell.requestFullscreen) {
      opts.toast?.('当前浏览器不支持全屏，已保留普通布局', true);
      return;
    }
    try {
      await shell.requestFullscreen();
    } catch {
      opts.toast?.('无法进入全屏，请检查浏览器权限；普通模式仍可用', true);
    }
  }

  function onFullscreenChange() {
    const shell = qs('.dash-shell');
    const active = document.fullscreenElement === shell;
    shell?.classList.toggle('is-fullscreen', active);
    document.body.classList.toggle('dash-fullscreen', active);
    const enter = qs('[data-act="fullscreen"]');
    const exit = qs('[data-act="exit-fs"]');
    if (enter) enter.hidden = active;
    if (exit) exit.hidden = !active;
    if (!active) qs('#dash-evidence')?.close();
  }

  return {mount, unmount, isMounted: () => mounted};
})();
