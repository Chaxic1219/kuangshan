<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import {
  createFrameGate,
  frameRegions,
  isStaleFrame,
  detectionStroke,
  formatDetectionCaption
} from "./frame.mjs";

const props = defineProps<{
  camera?: any;
  record?: any;
  regions?: any[];
  preview?: boolean;
}>();

const gate = createFrameGate();
const src = ref("");
const recordView = ref<any>(null);
const zones = ref<any[]>([]);
const loaded = ref(false);
const error = ref("");
const loading = ref(false);

const selected = computed(
  () =>
    props.record ??
    (Object.values(props.camera?.latest || {}).sort((a: any, b: any) =>
      String(b.capturedAt).localeCompare(String(a.capturedAt))
    )[0] as any)
);
const video = computed(() =>
  props.preview && props.camera?.preview?.kind === "video"
    ? props.camera.preview.url
    : ""
);
const status = computed(
  () =>
    props.camera?.lastFailure ||
    (props.camera?.running ? "等待检测画面" : "尚未开始分析")
);

/** 只跟踪会改变画面来源的字段，避免整单 JSON 深拷贝拖慢弹窗。 */
watch(
  () => ({
    cameraId: props.camera?.id || "",
    image: selected.value?.image || props.camera?.cover || "",
    recordId: selected.value?.id || selected.value?.frameId || "",
    capturedAt: selected.value?.capturedAt || "",
    detCount: selected.value?.detections?.length || 0,
    regionKey: JSON.stringify(props.regions ?? null),
    video: video.value
  }),
  dep => {
    gate.next();
    error.value = "";
    if (dep.video) {
      src.value = "";
      recordView.value = null;
      zones.value = [];
      loaded.value = false;
      loading.value = false;
      return;
    }
    const record = selected.value
      ? {
          ...selected.value,
          detections: [...(selected.value.detections || [])],
          regions: [...(selected.value.regions || [])]
        }
      : null;
    const next = record ? record.image : props.camera?.cover || "";
    if (!next) {
      src.value = "";
      recordView.value = record;
      zones.value = [];
      loaded.value = false;
      loading.value = false;
      error.value = record ? "此记录缺少图片证据" : "";
      return;
    }
    recordView.value = record;
    zones.value = frameRegions(props.camera, record, props.regions);
    if (src.value !== next) {
      loaded.value = false;
      loading.value = true;
      src.value = next;
    }
  },
  { immediate: true }
);

function onImageLoad() {
  loaded.value = true;
  loading.value = false;
  error.value = "";
}
function onImageError() {
  loading.value = false;
  loaded.value = false;
  error.value = "图片加载失败，当前证据不可用";
}

onBeforeUnmount(() => {
  gate.next();
});

function points(zone: any) {
  return (zone.points || []).map((p: number[]) => `${p[0]},${p[1]}`).join(" ");
}
</script>

<template>
  <div class="detection-frame" :aria-busy="loading">
    <video
      v-if="video"
      :src="video"
      controls
      autoplay
      muted
      loop
      playsinline
      @error="error = '原视频预览加载失败'"
    />
    <div v-else-if="src" class="stage">
      <img
        class="frame-image"
        :src="src"
        alt="检测画面"
        decoding="async"
        @load="onImageLoad"
        @error="onImageError"
      />
      <svg
        class="overlay"
        viewBox="0 0 1 1"
        preserveAspectRatio="none"
        aria-hidden="true"
      >
        <polygon
          v-for="zone in zones"
          :key="zone.id"
          :points="points(zone)"
          fill="#20c7ac"
          fill-opacity=".15"
          stroke="#20c7ac"
          stroke-width="0.004"
        />
        <g
          v-for="(d, index) in recordView?.detections || []"
          :key="index"
          :stroke="detectionStroke(d)"
        >
          <template v-if="d.bbox">
            <rect
              :x="d.bbox[0]"
              :y="d.bbox[1]"
              :width="d.bbox[2] - d.bbox[0]"
              :height="d.bbox[3] - d.bbox[1]"
              fill="none"
              stroke-width="0.004"
            />
            <text
              :x="d.bbox[0] + 0.008"
              :y="Math.max(0.03, d.bbox[1] - 0.012)"
              font-size="0.035"
              fill="#fff"
              stroke="#102633"
              stroke-width="0.002"
              paint-order="stroke"
            >
              {{ formatDetectionCaption(d) }}
            </text>
          </template>
        </g>
      </svg>
      <p v-if="loading && !loaded" class="notice">正在加载画面…</p>
    </div>
    <p v-else class="empty">{{ loading ? "正在加载画面…" : error || status }}</p>
    <p v-if="error && src" class="notice">{{ error }}</p>
    <p v-if="error && video" class="notice">{{ error }}</p>
    <p
      v-if="
        camera?.lastFailure || Object.keys(camera?.modelErrors || {}).length
      "
      class="failure"
    >
      {{ camera?.lastFailure || "模型推理失败，请检查运行日志" }}
    </p>
    <p v-if="isStaleFrame(camera, recordView)" class="notice">
      画面已停更 ·
      {{ new Date(recordView.capturedAt).toLocaleString("zh-CN") }}
    </p>
  </div>
</template>

<style scoped>
.detection-frame {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 100%;
  height: 100%;
  min-height: 180px;
  background: var(--vision-media-bg, #09151f);
  overflow: hidden;
  border-radius: 8px;
}
.stage {
  position: relative;
  display: inline-block;
  max-width: 100%;
  max-height: 70vh;
  line-height: 0;
}
.frame-image {
  display: block;
  max-width: 100%;
  max-height: 70vh;
  width: auto;
  height: auto;
  object-fit: contain;
  background: var(--vision-media-bg, #09151f);
}
.overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.detection-frame video {
  display: block;
  width: 100%;
  height: 100%;
  max-height: 70vh;
  object-fit: contain;
}
.empty {
  padding: 28px;
  color: var(--vision-media-fg, #a3b6c5);
  text-align: center;
}
.notice {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  margin: 0;
  padding: 8px;
  background: #19242de6;
  color: #ffd48b;
  font-size: 12px;
  line-height: 1.4;
}
.failure {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  margin: 0;
  padding: 8px;
  background: #53252ce6;
  color: #ffd5d5;
  font-size: 12px;
}
</style>
