<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import AnnotatedLive from "@/components/vision/AnnotatedLive.vue";
import LivePlayer from "@/components/vision/LivePlayer.vue";
import {
  detectionStroke,
  formatDetectionCaption,
  frameRegions
} from "@/components/vision/frame.mjs";
import {
  canUseSmoothBase,
  detectionLagMs,
  formatDetectionLag,
  latestRecord
} from "@/components/vision/smooth-live.mjs";
import { useVisionStore } from "@/stores/vision";

const props = defineProps<{
  camera: any;
  layout?: "single" | "grid";
}>();

const vision = useVisionStore();
const forceMjpeg = ref(false);
const fallbackHint = ref("");
const nowMs = ref(Date.now());
const natural = ref<{ w: number; h: number } | null>(null);
/** WebRTC 握手期间用封面/分析截帧占位，避免整段黑屏。 */
const streamReady = ref(false);
let tick: ReturnType<typeof setInterval> | null = null;

const placeholder = computed(
  () => props.camera?.liveImage || props.camera?.cover || ""
);

const preferBase = computed(
  () => canUseSmoothBase(props.camera?.preview) && !forceMjpeg.value
);

const stageStyle = computed(() => {
  const size = natural.value;
  if (!size) {
    return {
      width: "100%",
      minHeight: props.layout === "single" ? "240px" : "160px",
      aspectRatio: "16 / 9"
    };
  }
  const ratio = size.w / size.h;
  if (props.layout === "grid") {
    return {
      aspectRatio: `${size.w} / ${size.h}`,
      width: "100%",
      marginInline: "auto"
    };
  }
  return {
    aspectRatio: `${size.w} / ${size.h}`,
    width: `min(100%, calc(70vh * ${ratio}))`,
    maxWidth: "100%",
    maxHeight: "70vh",
    marginInline: "auto"
  };
});

const record = computed(() => latestRecord(props.camera));
const zones = computed(() =>
  frameRegions(props.camera, record.value, undefined)
);
const lag = computed(() =>
  formatDetectionLag(detectionLagMs(record.value, nowMs.value))
);

watch(
  () => props.camera?.id,
  () => {
    forceMjpeg.value = false;
    fallbackHint.value = "";
    streamReady.value = false;
    // 保留上一路比例，避免切换时舞台先缩成黑条再撑开。
  }
);

watch(
  () => props.camera?.preview?.url || props.camera?.preview?.whep || "",
  () => {
    streamReady.value = false;
  }
);

function onBaseFail(message: string) {
  forceMjpeg.value = true;
  streamReady.value = false;
  fallbackHint.value = message
    ? `已回退分析预览（${message}）`
    : "已回退分析预览";
}

function onMeta(size: { w: number; h: number }) {
  if (size?.w > 0 && size?.h > 0) natural.value = size;
}

function onBaseLoading() {
  streamReady.value = false;
}

function onBaseReady() {
  streamReady.value = true;
}

function points(zone: any) {
  return (zone.points || []).map((p: number[]) => `${p[0]},${p[1]}`).join(" ");
}

onMounted(() => {
  // 放大页需要更密的 latest，否则框只能跟 1.5s 轮询，观感必顿。
  vision.setPollInterval(250);
  tick = setInterval(() => {
    nowMs.value = Date.now();
  }, 250);
});
onBeforeUnmount(() => {
  vision.setPollInterval(1500);
  if (tick) clearInterval(tick);
});
</script>

<template>
  <div class="smooth-live">
    <div v-if="preferBase" class="smooth-stage" :style="stageStyle">
      <!-- 加载页：Element Plus v-loading；底层可带模糊封面 -->
      <div
        v-show="!streamReady"
        class="stream-loading"
        v-loading="true"
        element-loading-text="正在加载视频流…"
        element-loading-background="rgba(11, 18, 32, 0.72)"
      >
        <img
          v-if="placeholder"
          class="loading-cover"
          :src="placeholder"
          alt=""
        />
        <el-skeleton v-else animated class="loading-skeleton">
          <template #template>
            <el-skeleton-item
              variant="image"
              style="width: 100%; height: 100%"
            />
          </template>
        </el-skeleton>
      </div>

      <!-- 后台建连；就绪后再显示 -->
      <LivePlayer
        embedded
        fit="fill"
        class="base-player"
        :class="{ ready: streamReady }"
        :preview="camera.preview"
        @fail="onBaseFail"
        @meta="onMeta"
        @loading="onBaseLoading"
        @ready="onBaseReady"
      />

      <template v-if="streamReady">
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
            v-for="(d, index) in record?.detections || []"
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
        <span
          v-if="lag?.text"
          class="lag-badge"
          :class="lag.level"
          >{{ lag.text }}</span
        >
        <span v-else-if="!record" class="lag-badge info">等待检测结果</span>
      </template>
    </div>
    <template v-else>
      <AnnotatedLive :camera="camera" :layout="layout || 'single'" />
      <p v-if="fallbackHint" class="fallback-hint">{{ fallbackHint }}</p>
    </template>
  </div>
</template>

<style scoped>
.smooth-live {
  position: relative;
  width: 100%;
}
.smooth-stage {
  position: relative;
  background: var(--vision-media-bg, #0b1220);
  overflow: hidden;
}
.stream-loading {
  position: absolute;
  inset: 0;
  z-index: 4;
  background: #0b1220;
}
.loading-cover {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
  filter: blur(6px) brightness(0.55);
  transform: scale(1.04);
}
.loading-skeleton {
  width: 100%;
  height: 100%;
}
.loading-skeleton :deep(.el-skeleton__item) {
  height: 100%;
  border-radius: 0;
}
.base-player {
  opacity: 0;
  pointer-events: none;
  transition: opacity 0.2s ease;
  z-index: 1;
}
.base-player.ready {
  opacity: 1;
  pointer-events: auto;
}
.overlay {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 2;
}
.lag-badge {
  position: absolute;
  top: 10px;
  left: 10px;
  z-index: 3;
  padding: 4px 8px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.2;
  color: #fff;
  background: #0f172acc;
}
.lag-badge.warn {
  background: #b45309cc;
}
.lag-badge.info {
  background: #0f172acc;
}
.fallback-hint {
  margin: 8px 0 0;
  text-align: center;
  font-size: 12px;
  color: var(--el-color-warning);
}
</style>
