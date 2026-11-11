<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { pointInImage } from "./frame.mjs";
const props = defineProps<{ modelValue: any[]; image: string }>();
const emit = defineEmits(["update:modelValue", "drawing"]);
const svg = ref<SVGSVGElement>();
const width = ref(0),
  height = ref(0),
  drawing = ref(false),
  selected = ref("");
const points = ref<number[][]>([]);
const dragging = ref<number | null>(null);
const imageError = ref(false);
const selectedRegion = computed(() =>
  props.modelValue.find(z => z.id === selected.value)
);
watch(
  () => props.image,
  () => {
    width.value = height.value = 0;
    imageError.value = false;
  }
);
watch(
  [drawing, points],
  () => emit("drawing", drawing.value && points.value.length > 0),
  { deep: true }
);
const list = () => JSON.parse(JSON.stringify(props.modelValue));
const polygon = (items: number[][]) =>
  items.map(p => `${p[0] * 1000},${p[1] * 1000}`).join(" ");
function update(id: string, key: string, value: any) {
  const zones = list();
  zones.find(z => z.id === id)[key] = value;
  emit("update:modelValue", zones);
}
function cancel() {
  drawing.value = false;
  points.value = [];
}
function finish() {
  if (points.value.length < 3) return;
  const id = `zone_${crypto.randomUUID().replaceAll("-", "").slice(0, 16)}`;
  emit("update:modelValue", [
    ...list(),
    {
      id,
      name: `布控区域 ${props.modelValue.length + 1}`,
      enabled: true,
      points: points.value.map(p => [...p])
    }
  ]);
  selected.value = id;
  cancel();
}
function position(e: PointerEvent, clamp = false) {
  return pointInImage(
    e.clientX,
    e.clientY,
    svg.value!.getBoundingClientRect(),
    width.value,
    height.value,
    clamp
  );
}
function down(e: PointerEvent) {
  if (e.button !== 0 || !width.value) return;
  const p = position(e);
  if (!p) return;
  if (drawing.value) {
    if (
      points.value.length >= 3 &&
      Math.hypot(p[0] - points.value[0][0], p[1] - points.value[0][1]) < 0.025
    ) {
      finish();
      return;
    }
    if (points.value.length < 64) points.value.push(p);
  }
}
function drag(e: PointerEvent, index: number) {
  if (drawing.value) return;
  dragging.value = index;
  svg.value?.setPointerCapture(e.pointerId);
}
function move(e: PointerEvent) {
  if (dragging.value === null || !selectedRegion.value) return;
  const p = position(e, true);
  if (!p) return;
  const changed = selectedRegion.value.points.map((v: number[]) => [...v]);
  changed[dragging.value] = p;
  update(selected.value, "points", changed);
}
function loaded(e: Event) {
  const image = e.target as HTMLImageElement;
  width.value = image.naturalWidth;
  height.value = image.naturalHeight;
}
</script>

<template>
  <div>
    <div
      class="stage"
      :style="{ aspectRatio: width ? `${width}/${height}` : '16/9' }"
    >
      <img
        :src="image"
        alt="区域绘制参考画面"
        @load="loaded"
        @error="imageError = true"
      />
      <svg
        ref="svg"
        viewBox="0 0 1000 1000"
        preserveAspectRatio="none"
        aria-label="点击添加顶点，拖动白色顶点调整区域"
        @pointerdown.prevent="down"
        @pointermove="move"
        @pointerup="dragging = null"
        @pointercancel="dragging = null"
      >
        <g v-for="zone in modelValue" :key="zone.id">
          <polygon
            :points="polygon(zone.points)"
            :fill="zone.enabled ? '#20c7ac' : '#87929c'"
            fill-opacity=".18"
            :stroke="selected === zone.id ? '#ffd16e' : '#20c7ac'"
            stroke-width="2"
            vector-effect="non-scaling-stroke"
            @click="!drawing && (selected = zone.id)"
          />
        </g>
        <polyline
          v-if="drawing"
          :points="polygon(points)"
          fill="none"
          stroke="#ffd16e"
          stroke-width="2"
          vector-effect="non-scaling-stroke"
        />
        <circle
          v-for="(p, i) in drawing ? points : selectedRegion?.points || []"
          :key="i"
          :cx="p[0] * 1000"
          :cy="p[1] * 1000"
          r="8"
          fill="#fff"
          stroke="#078b7b"
          @pointerdown.stop="drawing ? down($event) : drag($event, i)"
        />
      </svg>
    </div>
    <el-alert
      v-if="imageError"
      title="封面加载失败，请返回第一步重新测试连接"
      type="error"
      :closable="false"
    />
    <div class="tools">
      <el-button
        :disabled="!width || modelValue.length >= 32 || drawing"
        @click="
          drawing = true;
          selected = '';
        "
        >绘制区域</el-button
      >
      <el-button :disabled="!drawing || !points.length" @click="points.pop()"
        >撤销顶点</el-button
      >
      <el-button
        :disabled="!drawing || points.length < 3"
        type="primary"
        @click="finish"
        >闭合区域</el-button
      >
      <el-button :disabled="!drawing" @click="cancel">取消绘制</el-button>
    </div>
    <p class="hint">
      逐点绘制，至少 3
      点；点击起点或“闭合区域”完成。选中区域后可拖动白色顶点。最多 32
      个区域，每个区域最多 64 点。
    </p>
    <div v-for="zone in modelValue" :key="zone.id" class="zone">
      <el-switch
        :model-value="zone.enabled !== false"
        active-text="启用"
        @update:model-value="update(zone.id, 'enabled', $event)"
      />
      <el-input
        :model-value="zone.name"
        maxlength="80"
        aria-label="区域名称"
        @update:model-value="update(zone.id, 'name', $event)"
      />
      <el-button :disabled="drawing" @click="selected = zone.id"
        >编辑顶点</el-button
      >
      <el-button
        type="danger"
        plain
        @click="
          emit(
            'update:modelValue',
            modelValue.filter(z => z.id !== zone.id)
          )
        "
        >删除</el-button
      >
    </div>
  </div>
</template>

<style scoped>
.stage {
  position: relative;
  width: 100%;
  background: #0b1822;
  overflow: hidden;
}
.stage img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.stage svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  touch-action: none;
}
.tools {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 12px;
}
.tools .el-button {
  margin-left: 0;
}
.hint {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.zone {
  display: flex;
  gap: 12px;
  align-items: center;
  margin: 12px 0;
}
.zone .el-input {
  flex: 1;
}
@media (max-width: 600px) {
  .zone {
    flex-wrap: wrap;
  }
}
</style>
