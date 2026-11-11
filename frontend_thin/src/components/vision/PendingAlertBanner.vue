<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { request } from "@/api/vision";
import { riskLabels } from "./alert-utils.mjs";

const MUTE_KEY = "vision-pending-alert-muted";
const route = useRoute();
const router = useRouter();
const muted = ref(
  typeof localStorage !== "undefined" && localStorage.getItem(MUTE_KEY) === "1"
);
const summary = ref<any>({ pendingCount: 0, highestRisk: "" });
let timer: ReturnType<typeof setInterval>;
let prevCount = 0;

const onDesk = computed(
  () =>
    route.path.startsWith("/alert-desk") || route.name === "AlertDesk"
);
const visible = computed(
  () => !onDesk.value && (summary.value.pendingCount || 0) > 0
);

watch(muted, value => {
  try {
    localStorage.setItem(MUTE_KEY, value ? "1" : "0");
  } catch {
    /* ignore */
  }
});

function beep() {
  if (muted.value || typeof window === "undefined") return;
  try {
    const ctx = new (window.AudioContext || (window as any).webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.type = "sine";
    osc.frequency.value = 880;
    gain.gain.value = 0.05;
    osc.connect(gain);
    gain.connect(ctx.destination);
    osc.start();
    osc.stop(ctx.currentTime + 0.18);
  } catch {
    /* 忽略自动播放限制 */
  }
}

async function poll() {
  if (typeof document !== "undefined" && document.hidden) return;
  try {
    const next = await request("/api/alerts/pending-summary");
    if (next.pendingCount > prevCount && next.pendingCount > 0) beep();
    prevCount = next.pendingCount || 0;
    summary.value = next;
  } catch {
    /* 保持上次摘要 */
  }
}

onMounted(() => {
  void poll();
  timer = setInterval(() => void poll(), 15000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <div v-if="visible" class="pending-banner" role="status">
    <el-alert
      type="warning"
      show-icon
      :closable="false"
      :title="`未处置告警 ${summary.pendingCount} 条 · 最高风险 ${riskLabels[summary.highestRisk] || summary.highestRisk || '—'}`"
    >
      <template #default>
        <div class="pending-actions">
          <el-switch
            v-model="muted"
            inline-prompt
            active-text="静音"
            inactive-text="提示音"
          />
          <el-button type="primary" size="small" @click="router.push('/alert-desk')"
            >去处置</el-button
          >
        </div>
      </template>
    </el-alert>
  </div>
</template>

<style scoped>
.pending-banner {
  flex: 0 0 auto;
  width: 100%;
  box-sizing: border-box;
  padding: 12px 16px 0;
  position: relative;
  z-index: 1;
}
.pending-actions {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-top: 6px;
  flex-wrap: wrap;
}
</style>
