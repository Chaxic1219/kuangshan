<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

const props = defineProps<{
  camera: { id?: string; liveStream?: string; liveImage?: string; name?: string };
  /** 单路尽量铺满可用宽度；多路卡片内按比例适配 */
  layout?: "single" | "grid";
}>();

const pageVisible = ref(
  typeof document === "undefined" ? true : !document.hidden
);
const src = computed(() => {
  if (!pageVisible.value) return "";
  return props.camera?.liveStream || props.camera?.liveImage || "";
});
const natural = ref<{ w: number; h: number } | null>(null);

function onVisibility() {
  pageVisible.value = !document.hidden;
}

watch(
  () => props.camera?.id,
  () => {
    natural.value = null;
  }
);

onMounted(() => document.addEventListener("visibilitychange", onVisibility));
onBeforeUnmount(() =>
  document.removeEventListener("visibilitychange", onVisibility)
);

function onLoad(event: Event) {
  const img = event.target as HTMLImageElement;
  if (img.naturalWidth > 0 && img.naturalHeight > 0) {
    natural.value = { w: img.naturalWidth, h: img.naturalHeight };
  }
}

/** 按推流真实比例定盒：宽度吃满可用区，高度随比例；过高则缩宽，避免黑边。 */
const wrapStyle = computed(() => {
  const size = natural.value;
  if (!size) {
    return {
      width: "100%",
      minHeight: props.layout === "single" ? "240px" : "160px"
    };
  }
  const ratio = size.w / size.h;
  // 单路：宽度优先铺满，超高则按 70vh 缩宽；多路：卡片内 100% 宽 + 真实比例。
  if (props.layout === "single") {
    return {
      aspectRatio: `${size.w} / ${size.h}`,
      width: `min(100%, calc(70vh * ${ratio}))`,
      maxWidth: "100%",
      maxHeight: "70vh",
      marginInline: "auto"
    };
  }
  return {
    aspectRatio: `${size.w} / ${size.h}`,
    width: "100%",
    marginInline: "auto"
  };
});
</script>

<template>
  <div class="annotated-live-wrap" :style="wrapStyle">
    <img
      v-if="src"
      class="annotated-live"
      :src="src"
      :alt="`${camera.name || '视频'} 带检测框实时预览`"
      @load="onLoad"
    />
    <div v-else class="annotated-live-empty">等待带检测框的实时画面…</div>
  </div>
</template>

<style scoped>
.annotated-live-wrap {
  position: relative;
  background: var(--vision-media-bg, #0b1220);
  overflow: hidden;
}
.annotated-live {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: fill;
}
.annotated-live-empty {
  display: grid;
  place-items: center;
  width: 100%;
  min-height: 160px;
  color: var(--vision-media-fg, var(--el-text-color-secondary));
  background: var(--vision-media-bg, #0b1220);
  font-size: 14px;
}
</style>
