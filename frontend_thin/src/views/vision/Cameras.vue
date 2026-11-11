<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { ElMessage, ElMessageBox } from 'element-plus';
import { useVisionStore } from '@/stores/vision';
import { request } from '@/api/vision';
import CameraEditor from '@/components/vision/CameraEditor.vue';
import { statusText, statusType } from './cameraStatus';
const vision = useVisionStore();
const router = useRouter();
const search = ref('');
const editing = ref(false);
const camera = ref<any>(null);
const busy = ref('');
const cameras = computed(() => vision.state.cameras.filter(c => `${c.name} ${c.location || ''}`.toLowerCase().includes(search.value.toLowerCase())));
const running = computed(() => vision.state.cameras.filter(c => c.running).length);
const taskCount = computed(() => vision.state.cameras.reduce((n, c) => n + c.tasks.filter(t => t.enabled).length, 0));
function edit(item = null) { camera.value = item; editing.value = true; }
async function saved() { editing.value = false; await vision.refresh(); }
async function action(item, name: 'start' | 'stop' | 'delete') {
  if (busy.value) return;
  if (name === 'delete') {
    try { await ElMessageBox.confirm(`删除“${item.name}”的视频配置？历史告警仍会保留。`, '删除视频', { type: 'warning', confirmButtonText: '删除', cancelButtonText: '取消' }); }
    catch { return; }
  }
  busy.value = item.id;
  try {
    await request(`/api/cameras/${name}`, { id: item.id });
    ElMessage.success(name === 'start' ? '已提交启动分析' : name === 'stop' ? '已停止分析' : '视频已删除');
  } catch (e) { ElMessage.error(e.message); }
  finally { await vision.refresh(); busy.value = ''; }
}
</script>
<template>
  <div class="vision-page">
    <header class="vision-heading"><div><span class="vision-kicker">安全监测中心 / 视频资源</span><h1>视频管理</h1><p>连接每一路现场画面，统一管理检测场景与区域布控。</p></div><el-button type="primary" size="large" :disabled="!vision.connected" @click="edit()">＋ 添加视频</el-button></header>
    <el-alert v-if="!vision.connected" class="vision-error" :title="vision.error || '正在连接本地服务…'" type="warning" :closable="false" show-icon />
    <div class="vision-summary"><article><span>已配置视频</span><strong>{{ vision.state.cameras.length }}</strong></article><article><span>运行中视频</span><strong>{{ running }}</strong></article><article><span>已启用场景</span><strong>{{ taskCount }}</strong></article><article><span>累计告警事件</span><strong>{{ vision.state.alertCount ?? '—' }}</strong></article></div>
    <div class="vision-toolbar"><div><b>视频资源</b><span class="vision-muted">　共 {{ cameras.length }} 路</span></div><el-input v-model="search" clearable placeholder="搜索名称或安装位置" aria-label="搜索视频" /></div>
    <div class="vision-camera-grid">
      <article v-for="item in cameras" :key="item.id" class="vision-card">
        <div class="vision-card-picture" role="button" tabindex="0" :aria-label="`监控 ${item.name}`" @click="router.push({ path: '/monitor', query: { cameraId: item.id } })" @keydown.enter="router.push({ path: '/monitor', query: { cameraId: item.id } })">
          <img v-if="item.cover" class="camera-cover" :src="item.cover" :alt="item.name" />
          <div v-else class="camera-cover-empty">暂无封面</div>
          <el-tag class="vision-card-status" :type="statusType(item)" effect="dark">{{ statusText(item) }}</el-tag>
        </div>
        <div class="vision-card-body"><h2>{{ item.name }}</h2><p class="vision-muted">{{ item.location || '尚未填写安装位置' }}</p><div class="tags"><el-tag v-for="task in item.tasks" :key="task.eventId" :type="task.enabled ? 'info' : 'warning'" size="small">{{ vision.state.events[task.eventId]?.name || task.eventId }}{{ task.enabled ? '' : ' · 已停用' }}</el-tag><span v-if="!item.tasks.length" class="vision-muted">尚未绑定检测场景</span></div><p class="vision-muted">{{ item.regions.length }} 个布控区域 · {{ item.liveDeviceId ? '本机摄像头 / RTSP' : '视频流 / 本地素材' }}</p><el-alert v-if="item.error" :title="String(item.error)" type="error" :closable="false" />
          <div class="vision-card-actions"><el-button :disabled="!vision.connected || !!busy" :loading="busy === item.id" :type="item.running ? 'warning' : 'primary'" @click="action(item, item.running ? 'stop' : 'start')">{{ item.running ? '停止分析' : '启动分析' }}</el-button><el-button :disabled="!vision.connected || !!busy" @click="edit(item)">配置</el-button><el-button text type="danger" :disabled="!vision.connected || !!busy" @click="action(item, 'delete')">删除</el-button></div>
        </div>
      </article>
    </div>
    <el-empty v-if="vision.connected && !cameras.length" :description="search ? '没有匹配的视频' : '暂无视频，添加第一路摄像头开始监测'" />
    <p class="vision-muted" style="margin-top:24px">本地推理 · 页面操作不会自动启用新告警规则。{{ vision.state.capacity?.storage?.level === 'blocked' ? '磁盘空间不足，请先清理。' : '' }}</p>
    <CameraEditor v-if="editing" :camera="camera" :state="vision.state" :connected="vision.connected" @close="editing = false" @saved="saved" />
  </div>
</template>
