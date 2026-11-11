const names = { starting: '启动中', connecting: '连接中', streaming: '已取流', running: '分析中', reconnecting: '重连中', stopped: '未运行', error: '启动失败', backoff: '自动恢复中', failed: '已失败' };
export function statusText(camera: any): string {
  const state = ['backoff', 'failed'].includes(camera.lifecycle) ? camera.lifecycle : camera.state;
  return names[state] || (camera.running ? '分析中' : '未运行');
}
export function statusType(camera: any): 'success' | 'danger' | 'warning' | 'info' {
  const text = statusText(camera);
  return text === '分析中' ? 'success' : ['启动失败', '已失败'].includes(text) ? 'danger' : camera.running ? 'warning' : 'info';
}
