<script setup lang="ts">
import { reactive, ref, watch } from "vue";
import {
  presets,
  wallTime,
  fromWallTime,
  validateRange
} from "./alert-utils.mjs";
const props = defineProps<{
  modelValue: any;
  cameras?: any[];
  events?: any[];
  locations?: string[];
  range?: any;
}>();
const emit = defineEmits(["apply"]);
const draft = reactive({
  preset: "today",
  from: "",
  to: "",
  cameraId: "",
  location: "",
  eventId: "",
  disposition: "",
  riskLevel: ""
});

const error = ref("");
watch(
  () => props.modelValue,
  value => {
    Object.assign(draft, value, {
      from: wallTime(value.from),
      to: wallTime(value.to)
    });
  },
  { immediate: true, deep: true }
);
function changePreset() {
  if (draft.preset === "custom" && !draft.from && props.range) {
    draft.from = wallTime(props.range.from);
    draft.to = wallTime(props.range.to);
  }
}
function apply() {
  try {
    const value = { ...draft };
    if (value.preset === "custom") {
      value.from =
        value.from === wallTime(props.modelValue.from)
          ? props.modelValue.from
          : fromWallTime(value.from);
      value.to =
        value.to === wallTime(props.modelValue.to)
          ? props.modelValue.to
          : fromWallTime(value.to);
      validateRange(value.from, value.to);
    }
    error.value = "";
    emit("apply", value);
  } catch (e) {
    error.value = e.message;
  }
}
</script>

<template>
  <form class="alert-filters" @submit.prevent="apply">
    <label
      >时间范围<el-select v-model="draft.preset" @change="changePreset"
        ><el-option
          v-for="p in presets"
          :key="p[0]"
          :value="p[0]"
          :label="p[1]" /></el-select
    ></label>
    <template v-if="draft.preset === 'custom'">
      <label
        >开始（UTC+8）<input
          v-model="draft.from"
          type="datetime-local"
          step="0.001"
          required
      /></label>
      <label
        >结束（不含，UTC+8）<input
          v-model="draft.to"
          type="datetime-local"
          step="0.001"
          required
      /></label>
    </template>
    <label
      >安装位置<el-select
        v-model="draft.location"
        clearable
        filterable
        placeholder="全部位置"
        ><el-option
          v-for="loc in locations || []"
          :key="loc"
          :value="loc"
          :label="loc" /></el-select
    ></label>
    <label
      >摄像头<el-select
        v-model="draft.cameraId"
        clearable
        placeholder="全部摄像头"
        ><el-option
          v-for="c in cameras || []"
          :key="c.cameraId"
          :value="c.cameraId"
          :label="
            (c.cameraName || c.cameraId) +
            (c.location ? ` · ${c.location}` : '') +
            (c.status === 'deleted' ? '（已删除）' : '')
          " /></el-select
    ></label>
    <label
      >告警类型<el-select
        v-model="draft.eventId"
        clearable
        placeholder="全部类型"
        ><el-option
          v-for="e in events || []"
          :key="e.eventId"
          :value="e.eventId"
          :label="e.eventName" /></el-select
    ></label>
    <label
      >处置状态<el-select
        v-model="draft.disposition"
        clearable
        placeholder="全部状态"
        ><el-option value="pending" label="未处置" /><el-option
          value="handled"
          label="已处置" /><el-option
          value="false_positive"
          label="误报" /></el-select
    ></label>
    <label
      >告警级别<el-select
        v-model="draft.riskLevel"
        clearable
        placeholder="全部级别"
        ><el-option value="high" label="高" /><el-option
          value="medium"
          label="中" /><el-option value="low" label="低" /></el-select
    ></label>
    <el-button native-type="submit" type="primary">应用筛选</el-button>
    <p v-if="error" role="alert" class="filter-error">{{ error }}</p>
  </form>
</template>

<style scoped>
.alert-filters {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  flex-wrap: wrap;
  margin-bottom: 16px;
  padding: 14px 16px;
  background: var(--vision-surface);
  border: 1px solid var(--vision-border);
  border-radius: 10px;
}
.alert-filters label {
  display: grid;
  gap: 6px;
  font-size: 12px;
  color: var(--vision-text-regular);
}
.el-select {
  width: 170px;
}
input {
  height: 32px;
  border: 1px solid var(--vision-border-strong);
  background: var(--vision-surface);
  color: var(--vision-text);
  padding: 4px 8px;
  border-radius: 6px;
}
input:focus {
  outline: 2px solid var(--vision-accent-soft);
  border-color: var(--vision-accent);
}
.filter-error {
  width: 100%;
  color: var(--vision-danger);
  margin: 0;
}
</style>
