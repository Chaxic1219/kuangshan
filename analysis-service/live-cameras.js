'use strict';

// 草稿独立于视频卡片刷新，防止轮询覆盖正在编辑的模型选择。
const LivePanel = (() => {
  let data = null, loading = false, operating = false, lastScan = 0, failure = '';
  const drafts = new Map(), results = new Map();
  const body = () => document.querySelector('#live-body');
  const camera = id => state.cameras.find(c => c.id === id);
  const choices = model => Object.entries(state.events).filter(([, e]) => e.model === model && !e.region);

  function draftFor(device) {
    if (!drafts.has(device.id)) {
      drafts.set(device.id, {selected: !!device.cameraId,
        model: device.selection?.model || '', events: [...(device.selection?.events || [])]});
    }
    return drafts.get(device.id);
  }

  function label(device) {
    let hash = 0;
    for (const char of device.id) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
    return (hash >>> 0).toString(16).padStart(8, '0').slice(-6).toUpperCase();
  }

  function rowStatus(device) {
    const c = camera(device.cameraId);
    const result = results.get(device.id);
    if (result?.status === 'waiting') return '等待出流与启动结果…';
    if (c?.running) return statusNames[statusOf(c)] || '分析启动中';
    if (device.connected === false) return '设备离线 · 配置已保留';
    if (result && !result.ok) return result.message;
    if (c?.lastFailure) return c.lastFailure;
    if (!device.usable) return device.error || '设备未提供可采集的视频类型';
    if (result?.status === 'starting' && !c) return result.message;
    return device.cameraId ? '配置已保存 · 分析未运行' : '已发现 · 尚未验证出流';
  }

  function render() {
    const target = body();
    if (!target) return;
    if (!data) {
      target.innerHTML = `<p role="status">${esc(failure || '正在检测本机摄像头…')}</p><button data-local-action="scan" ${loading ? 'disabled' : ''}>重新检测</button>`;
      return;
    }
    const stream = data.stream || {};
    const conflict = stream.active && stream.mode !== 'live';
    const streamText = conflict ? '模拟流正在运行，请先通过模拟流停止脚本停止。' :
      stream.running ? '本机推流服务已启动；各路状态以实际出帧和分析结果为准。' : '推流服务未启动；保存并启动后会逐路验证视频。';
    const rows = data.devices.map(device => {
      const draft = draftFor(device), c = camera(device.cameraId);
      const disabled = operating || !!c?.running || !device.usable;
      const options = state.models.map(m => `<option value="${esc(m.id)}" ${m.id === draft.model ? 'selected' : ''} ${m.ready ? '' : 'disabled'}>${esc(m.name)}${m.ready ? '' : '（未就绪）'}</option>`).join('');
      const events = choices(draft.model).map(([id, e]) => `<label><input type="checkbox" data-live-event="${esc(id)}" ${draft.events.includes(id) ? 'checked' : ''} ${disabled ? 'disabled' : ''}>${esc(e.name)}</label>`).join('');
      return `<tr data-device-id="${esc(device.id)}">
        <td><label class="live-device"><input type="checkbox" data-live-select aria-label="选择 ${esc(device.name)} ${label(device)}" ${draft.selected ? 'checked' : ''} ${operating || !device.usable ? 'disabled' : ''}><span><b>${esc(device.name)}</b><small>设备 ${label(device)}${device.connected === false ? ' · 离线' : ''}</small></span></label></td>
        <td><select data-live-model aria-label="${esc(device.name)} ${label(device)} 的模型" ${disabled ? 'disabled' : ''}><option value="">请选择模型</option>${options}</select></td>
        <td><div class="live-events" aria-label="检测场景">${events || '<span class="small">选择模型后设置场景</span>'}</div></td>
        <td><span data-local-status role="status">${esc(rowStatus(device))}</span>${device.url ? `<small class="live-url">${esc(device.url)}</small>` : ''}</td>
        <td>${c?.running ? '<button data-local-action="stop-camera">停止分析</button>' : '<button data-local-action="retry">启动此路</button>'}</td>
      </tr>`;
    }).join('');
    target.innerHTML = `<div class="live-toolbar"><p class="live-service ${conflict ? 'error' : ''}" role="status">${esc(streamText)}</p><button data-local-action="scan" ${loading || operating ? 'disabled' : ''}>${loading ? '检测中…' : '重新检测'}</button></div>
      ${failure || data.error ? `<p class="error" role="alert">${esc(failure || data.error)}</p>` : ''}
      ${rows ? `<div class="live-table-wrap"><table class="live-table"><caption class="small">每路选择一个模型，可启用该模型下多个检测场景。</caption><thead><tr><th scope="col">摄像头</th><th scope="col">模型</th><th scope="col">检测场景</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead><tbody>${rows}</tbody></table></div>` : '<div class="live-empty"><b>未发现本机摄像头</b><p>连接 USB 摄像头后点击“重新检测”。检测在运行服务的电脑上执行。</p></div>'}
      <div class="live-actions"><span class="small" data-local-count></span><button data-local-action="save">仅保存配置</button><button class="primary" data-local-action="start">${operating ? '正在逐路处理…' : '保存并启动'}</button></div>
      <p class="small">重复启动会复用原配置。区域入侵需到视频“配置”中绘制区域。仅保存不启动分析；推流服务已运行时，保存会应用映射并采集视频。</p>
      ${(data.livePaths || []).length ? `<details class="live-mappings"><summary>已有 RTSP 映射（高级）</summary><p class="small">移除只取消推流映射，视频配置和历史证据会保留。重复旧映射可在这里清理。</p>${data.livePaths.map(p => `<div class="live-toolbar"><code>${esc(p.url)}</code><button data-live-copy="${esc(p.url)}">复制地址</button><button class="danger" data-local-action="remove-path" data-path="${esc(p.path)}" ${operating ? 'disabled' : ''}>移除映射 ${esc(p.path)}</button></div>`).join('')}</details>` : ''}
      ${stream.running ? '<button class="danger" data-local-action="stop-stream">停止本机推流</button>' : ''}`;
    sync();
  }

  function sync() {
    const target = body();
    if (!target || !data) return;
    let count = 0;
    for (const device of data.devices) {
      const draft = draftFor(device);
      if (draft.selected && device.usable) count++;
    }
    const counter = target.querySelector('[data-local-count]');
    if (counter) counter.textContent = `已选 ${count} 路 · 分析容量 ${state.capacity?.running || 0}/${state.capacity?.maxRunning || 6}`;
    target.querySelectorAll('[data-device-id]').forEach(row => {
      const device = data.devices.find(d => d.id === row.dataset.deviceId);
      const c = camera(device.cameraId), draft = draftFor(device);
      row.querySelector('[data-local-status]').textContent = rowStatus(device);
      row.querySelectorAll('[data-live-model],[data-live-event]').forEach(el => { el.disabled = operating || !!c?.running || !device.usable; });
      const action = row.querySelector('[data-local-action]');
      action.dataset.localAction = c?.running ? 'stop-camera' : 'retry';
      action.textContent = c?.running ? '停止分析' : (results.get(device.id)?.ok === false ? '重试此路' : '启动此路');
      action.disabled = operating || (!c?.running && (!device.usable || !draft.model || !draft.events.length));
    });
    target.querySelectorAll('[data-local-action="save"],[data-local-action="start"]').forEach(el => {
      el.disabled = operating || count === 0 || !!(data.stream?.active && data.stream.mode !== 'live');
    });
    const stop = target.querySelector('[data-local-action="stop-stream"]');
    if (stop) stop.disabled = operating;
  }

  async function load(force = false) {
    if (loading) { render(); return; }
    if (!force && data && Date.now() - lastScan < 15000) { render(); return; }
    loading = true;
    failure = '';
    render();
    try {
      data = await api('/api/cameras/devices');
      lastScan = Date.now();
      for (const device of data.devices) draftFor(device);
    } catch (error) { failure = `检测失败：${error.message}`; }
    finally { loading = false; render(); }
  }

  async function submit(start, onlyId = null) {
    if (operating) return;
    const devices = data.devices.filter(d => onlyId ? d.id === onlyId : draftFor(d).selected && d.usable);
    const selections = devices.map(d => ({deviceId: d.id, model: draftFor(d).model, events: draftFor(d).events}));
    if (!selections.length) return;
    if (selections.some(s => !s.model || !s.events.length)) { toast('请为每台选中的摄像头选择模型和检测场景', true); return; }
    operating = true;
    failure = '';
    devices.forEach(d => results.set(d.id, {ok: true, status: 'waiting'}));
    render();
    try {
      const result = await api('/api/cameras/live/batch', {selections, start});
      result.results.forEach(r => {
        results.set(r.deviceId, r);
        const d = data.devices.find(d => d.id === r.deviceId);
        if (d) Object.assign(d, {cameraId: r.cameraId, url: r.url});
      });
      const passed = result.results.filter(r => r.ok).length;
      toast(`${passed}/${result.results.length} 路${start ? '已提交启动' : '已保存'}${result.ok ? '' : '，请查看各路错误'}`, !result.ok);
      await refresh();
      await load(true);
    } catch (error) {
      failure = error.message;
      devices.forEach(d => results.set(d.id, {ok: false, status: 'failed', message: error.message}));
    } finally { operating = false; render(); }
  }

  document.querySelector('#live-dialog').addEventListener('change', event => {
    const row = event.target.closest('[data-device-id]');
    if (!row) return;
    const draft = drafts.get(row.dataset.deviceId);
    if (event.target.hasAttribute('data-live-select')) draft.selected = event.target.checked;
    if (event.target.hasAttribute('data-live-model')) {
      draft.model = event.target.value;
      draft.events = choices(draft.model).map(([id]) => id);
      draft.selected = true;
      render();
    }
    if (event.target.hasAttribute('data-live-event')) {
      const id = event.target.dataset.liveEvent;
      draft.events = event.target.checked ? [...draft.events, id] : draft.events.filter(e => e !== id);
    }
    sync();
  });

  document.querySelector('#live-dialog').addEventListener('click', async event => {
    const button = event.target.closest('[data-local-action]');
    if (!button || button.disabled) return;
    const action = button.dataset.localAction;
    const id = button.closest('[data-device-id]')?.dataset.deviceId;
    if (action === 'scan') return load(true);
    if (action === 'start' || action === 'save' || action === 'retry') return submit(action !== 'save', id);
    if (operating) return;
    operating = true;
    sync();
    try {
      if (action === 'stop-camera') {
        const device = data.devices.find(d => d.id === id);
        await api('/api/cameras/stop', {id: device.cameraId});
        results.delete(id);
      } else if (action === 'remove-path') {
        await api('/api/cameras/live/remove', {path: button.dataset.path});
      } else if (action === 'stop-stream') {
        if (!confirm('停止本机推流将使这些摄像头暂时无法分析，是否继续？')) return;
        for (const c of state.cameras.filter(c => c.liveDeviceId && c.running)) await api('/api/cameras/stop', {id: c.id});
        await api('/api/cameras/live/streams', {action: 'stop'});
      }
      await refresh();
      await load(true);
    } catch (error) { failure = error.message; }
    finally { operating = false; render(); }
  });
  return {load, sync};
})();
