import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createClient } from '../src/api/client.mjs';

const reply = (status, data) => ({ ok: status < 400, status, json: async () => data });
test('初始化前拒绝写入且令牌不出现在URL中', async () => {
  const calls = [];
  const client = createClient(async (url, options) => { calls.push([url, options]); return reply(200, { token: 'local-secret' }); });
  await assert.rejects(client.request('/api/cameras/save', {}), /初始化/);
  assert.equal(calls.length, 0);
  await client.request('/api/state');
  await client.request('/api/cameras/save', { name: '测试' });
  assert.equal(calls[1][1].headers['X-Console-Token'], 'local-secret');
  assert.equal(calls[1][0], '/api/cameras/save');
});
test('403只清除令牌而不重放写操作', async () => {
  let calls = 0;
  const client = createClient(async url => { calls++; return url === '/api/state' ? reply(200, { token: 'old' }) : reply(403, { error: '校验失败' }); });
  await client.request('/api/state');
  await assert.rejects(client.request('/api/cameras/start', { id: 'a' }), /校验失败/);
  await assert.rejects(client.request('/api/cameras/start', { id: 'a' }), /初始化/);
  assert.equal(calls, 2);
});
test('请求只能使用本机API路径，失败不返回假数据', async () => {
  const client = createClient(async () => reply(500, { error: '后端异常' }));
  await assert.rejects(client.request('https://other/api/state'), /路径/);
  await assert.rejects(client.request('/api/state'), /后端异常/);
});
