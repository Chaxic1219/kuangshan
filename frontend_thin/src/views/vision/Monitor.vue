<script setup lang="ts">
import { computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useVisionStore } from "@/stores/vision";
import SmoothLive from "@/components/vision/SmoothLive.vue";
import { statusText, statusType } from "./cameraStatus";

const vision = useVisionStore();
const route = useRoute();
const router = useRouter();

const filterId = computed({
  get: () => String(route.query.cameraId || ""),
  set: value => {
    // 筛选只作用于封面网格；进入播放页需显式点击封面
    router.replace({
      path: "/monitor/index",
      query: value ? { cameraId: value } : {}
    });
  }
});

const playingId = computed(() =>
  route.query.live === "1" ? String(route.query.cameraId || "") : ""
);
const playing = computed(
  () => vision.state.cameras.find(c => c.id === playingId.value) || null
);
const covers = computed(() =>
  vision.state.cameras.filter(c => !filterId.value || c.id === filterId.value)
);

/** 放大页状态条：随 store 1.5s 刷新。 */
const playerTags = computed(() => {
  const camera = playing.value;
  if (!camera) return [] as { key: string; label: string; type: string }[];
  const latest = record(camera);
  const detCount = latest?.detections?.length ?? 0;
  const alarmOnFrame = !!latest?.alarm;
  const infer =
    latest?.inferenceMs != null ? Number(latest.inferenceMs) : null;
  const lagMs = latest?.capturedAt
    ? Date.now() - Date.parse(latest.capturedAt)
    : null;
  const camAlerts = (vision.state.alerts || []).filter(
    (a: any) => a.cameraId === camera.id
  );
  const pendingAlerts = camAlerts.filter(
    (a: any) => !a.disposition || a.disposition === "pending"
  ).length;
  const bridge = camera.alerts || {};
  const tags: { key: string; label: string; type: string }[] = [
    {
      key: "status",
      label: statusText(camera),
      type: statusType(camera)
    },
    {
      key: "targets",
      label: `目标 ${detCount}`,
      type: detCount > 0 ? "warning" : "info"
    },
    {
      key: "lag",
      label:
        lagMs == null || !Number.isFinite(lagMs)
          ? "延迟 —"
          : lagMs > 30000
            ? "延迟 停更"
            : `延迟 ${formatLag(lagMs)}`,
      type:
        lagMs == null
          ? "info"
          : lagMs > 30000
            ? "danger"
            : lagMs > 3000
              ? "warning"
              : "success"
    }
  ];
  if (infer != null && Number.isFinite(infer)) {
    tags.push({
      key: "infer",
      label: `推理 ${Math.round(infer)} ms`,
      type: infer > 200 ? "warning" : "info"
    });
  }
  tags.push({
    key: "alerts",
    label: `告警 ${pendingAlerts}`,
    type: pendingAlerts > 0 ? "danger" : "info"
  });
  if (bridge.reported != null || bridge.failed != null) {
    tags.push({
      key: "reported",
      label: `已上报 ${bridge.reported ?? 0}`,
      type: "info"
    });
  }
  if (latest?.eventName) {
    tags.push({
      key: "scene",
      label: alarmOnFrame ? `${latest.eventName} · 本帧告警` : latest.eventName,
      type: alarmOnFrame ? "danger" : "info"
    });
  }
  return tags;
});

function formatLag(ms: number) {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  return `${(ms / 1000).toFixed(ms >= 10000 ? 0 : 1)} s`;
}
function coverOf(camera: any) {
  return camera.cover || record(camera)?.image || "";
}
function record(camera: any) {
  const rows: any[] = Object.values(camera.latest || {});
  return rows.sort((a, b) =>
    String(b.capturedAt).localeCompare(String(a.capturedAt))
  )[0];
}
function age(camera: any) {
  const r = record(camera);
  if (!r?.capturedAt) return camera.running ? "分析中 · 点击放大播放" : "尚未开始分析";
  return Date.now() - Date.parse(r.capturedAt) > 30000
    ? "画面已停更 · 仍可点击查看"
    : `最近检测 ${new Date(r.capturedAt).toLocaleTimeString("zh-CN", { hour12: false })}`;
}
function openPlayer(id: string) {
  router.push({
    path: "/monitor/index",
    query: { cameraId: id, live: "1" }
  });
}
function backToCovers() {
  router.replace({ path: "/monitor/index" });
}</script>

<template>
  <div class="vision-page">
    <header class="vision-heading">
      <div>
        <span class="vision-kicker">现场监测 / 按需拉流</span>
        <h1>{{ playing ? playing.name : "实时监控" }}</h1>
        <p v-if="playing">
          单路放大播放 · 流畅底流叠检测框 · 可返回封面列表
        </p>
        <p v-else>
          默认显示静态封面；点击某一路封面后进入放大播放页。
        </p>
      </div>
      <div class="heading-actions">
        <el-button v-if="playing" type="primary" @click="backToCovers"
          >返回封面</el-button
        >
        <el-button @click="router.push('/cameras/index')">管理视频</el-button>
      </div>
    </header>

    <el-alert
      v-if="!vision.connected"
      class="vision-error"
      title="服务连接中断，当前画面可能已过期"
      type="warning"
      :closable="false"
      show-icon
    />

    <!-- 放大播放页 -->
    <section v-if="playing" class="player-page">
      <div class="player-tags" aria-label="实时监测指标">
        <el-tag
          v-for="tag in playerTags"
          :key="tag.key"
          :type="tag.type as any"
          effect="dark"
          round
          >{{ tag.label }}</el-tag
        >
      </div>
      <div class="player-stage">
        <SmoothLive
          v-if="playing.running"
          :camera="playing"
          layout="single"
        />
        <div v-else class="camera-cover-empty player-empty">
          分析未运行，暂无实时流
        </div>
      </div>
    </section>

    <!-- 封面网格 -->
    <template v-else>
      <div class="vision-toolbar">
        <el-select
          v-model="filterId"
          style="width: 280px"
          aria-label="筛选监控视频"
        >
          <el-option label="全部视频" value="" />
          <el-option
            v-for="c in vision.state.cameras"
            :key="c.id"
            :label="c.name"
            :value="c.id"
          />
        </el-select>
        <span class="vision-muted"
          >{{ covers.length }} 路封面 · 点击后进入放大播放</span
        >
      </div>
      <div class="vision-monitor-grid">
        <article v-for="camera in covers" :key="camera.id" class="vision-card">
          <div
            class="vision-card-body"
            style="
              display: flex;
              align-items: center;
              justify-content: space-between;
            "
          >
            <h2>{{ camera.name }}</h2>
            <el-tag :type="statusType(camera)">{{
              statusText(camera)
            }}</el-tag>
          </div>
          <div class="vision-card-picture is-cover">
            <button
              type="button"
              class="cover-hit"
              :aria-label="`放大播放 ${camera.name}`"
              @click="openPlayer(camera.id)"
            >
              <img
                v-if="coverOf(camera)"
                class="camera-cover"
                :src="coverOf(camera)"
                :alt="`${camera.name} 封面`"
              />
              <div v-else class="camera-cover-empty">
                暂无封面 · 点击放大播放
              </div>
              <span class="cover-hint">点击放大播放</span>
            </button>
          </div>
          <div class="vision-card-body">
            <p class="vision-muted">{{ age(camera) }}</p>
          </div>
        </article>
      </div>
      <el-empty
        v-if="!covers.length"
        description="没有可显示的视频，请先在视频管理中添加"
      />
    </template>
  </div>
</template>

<style scoped>
.heading-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.cover-hit {
  position: relative;
  display: block;
  width: 100%;
  height: 100%;
  margin: 0;
  padding: 0;
  border: 0;
  background: transparent;
  cursor: pointer;
  color: inherit;
}
.cover-hint {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  padding: 8px 10px;
  background: #102030cc;
  color: #e8f2fb;
  font-size: 12px;
  text-align: center;
  pointer-events: none;
}
.vision-card-picture.is-cover {
  position: relative;
  aspect-ratio: 16 / 10;
  min-height: 160px;
}
.vision-card-picture.is-cover .cover-hit,
.vision-card-picture.is-cover .camera-cover,
.vision-card-picture.is-cover .camera-cover-empty {
  width: 100%;
  height: 100%;
  max-height: none;
}
.cover-hit .camera-cover {
  object-fit: cover;
}
.player-page {
  display: grid;
  gap: 12px;
}
.player-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.player-stage {
  position: relative;
  width: 100%;
  min-height: 360px;
  background: var(--vision-media-bg, #09151f);
  border-radius: 10px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.player-stage :deep(.annotated-live-wrap) {
  width: min(100%, calc(78vh * 16 / 9));
  max-width: 100%;
  max-height: 78vh;
}
.player-empty {
  min-height: 360px;
}
</style>
