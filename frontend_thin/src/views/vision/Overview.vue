<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { request } from "@/api/vision";
import { alertQuery, dateText } from "@/components/vision/alert-utils.mjs";

const router = useRouter();
const preset = ref("last7d");
const busy = ref(false);
const error = ref("");
const data = ref<any>(null);
let timer: ReturnType<typeof setInterval>;

const colors = [
  "#d97706",
  "#0d9488",
  "#2563eb",
  "#dc2626",
  "#7c3aed",
  "#0891b2"
];

const typeSlices = computed(() => {
  let offset = 0;
  const total = Math.max(1, data.value?.summary?.totalEvents || 0);
  return (data.value?.byType || []).map((item: any, i: number) => {
    const size = (item.count / total) * 100;
    const result = { ...item, size, offset, color: colors[i % colors.length] };
    offset += size;
    return result;
  });
});

const periodOptions = [
  ["today", "今日"],
  ["last24h", "最近 24 小时"],
  ["last7d", "近 7 天"],
  ["last30d", "近 30 天"]
];

function rateText(rate: unknown) {
  if (rate == null || Number.isNaN(Number(rate))) return "—";
  return `${(Number(rate) * 100).toFixed(1)}%`;
}

function durationText(seconds: unknown) {
  if (seconds == null || Number.isNaN(Number(seconds))) return "—";
  const s = Math.max(0, Number(seconds));
  if (s < 60) return `${Math.round(s)} 秒`;
  if (s < 3600) return `${(s / 60).toFixed(1)} 分钟`;
  return `${(s / 3600).toFixed(1)} 小时`;
}

async function loadDashboard() {
  busy.value = true;
  error.value = "";
  try {
    const params = alertQuery({ preset: preset.value });
    data.value = await request("/api/alerts/dashboard?" + params);
  } catch (e: any) {
    error.value = e.message || "加载总览失败";
  } finally {
    busy.value = false;
  }
}

function openAlerts(extra: Record<string, string> = {}) {
  if (!data.value?.range) return;
  router.push({
    path: "/alerts",
    query: Object.fromEntries(
      alertQuery({ preset: "custom", ...extra }, data.value.range)
    )
  });
}

function openDesk() {
  router.push("/alert-desk");
}

onMounted(() => {
  void loadDashboard();
  timer = setInterval(() => void loadDashboard(), 15000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <section class="overview">
    <header class="heading">
      <div>
        <span class="kicker">值班首页</span>
        <h1>监控总览</h1>
        <p>近期告警统计 · 类型占比 · UTC+8</p>
      </div>
      <div class="actions">
        <el-select
          v-model="preset"
          style="width: 160px"
          aria-label="统计周期"
          @change="loadDashboard"
        >
          <el-option
            v-for="p in periodOptions"
            :key="p[0]"
            :value="p[0]"
            :label="p[1]"
          />
        </el-select>
        <el-button :loading="busy" @click="loadDashboard">刷新</el-button>
        <el-button type="primary" @click="openDesk">告警处置</el-button>
      </div>
    </header>

    <el-alert
      v-if="error"
      type="error"
      :closable="false"
      :title="error"
      show-icon
    />

    <div class="metrics">
      <button type="button" class="metric" @click="openAlerts()">
        <span>累计告警</span>
        <strong>{{ data?.summary?.totalEvents ?? "—" }}</strong>
        <small>{{ periodOptions.find(p => p[0] === preset)?.[1] || "" }}</small>
      </button>
      <button
        type="button"
        class="metric danger"
        @click="openAlerts()"
      >
        <span>高风险告警</span>
        <strong>{{ data?.summary?.highRiskCount ?? "—" }}</strong>
        <small>需优先处置</small>
      </button>
      <button
        type="button"
        class="metric"
        @click="openAlerts({ disposition: 'pending' })"
      >
        <span>告警处置率</span>
        <strong>{{ rateText(data?.summary?.dispositionRate) }}</strong>
        <small
          >已处置 {{ data?.summary?.disposedCount ?? 0 }} / 未处置
          {{ data?.summary?.pendingCount ?? 0 }}</small
        >
      </button>
      <button type="button" class="metric" @click="openDesk">
        <span>平均处置时长</span>
        <strong class="text">{{
          durationText(data?.summary?.avgDispositionSeconds)
        }}</strong>
        <small>产生至处置完成的周期均值</small>
      </button>
    </div>
    <p v-if="data" class="meta">
      已更新 {{ dateText(data.generatedAt) }} · 范围
      [{{ dateText(data.range.from) }}, {{ dateText(data.range.to) }})
    </p>

    <article class="panel chart-panel">
      <h2>告警统计分析</h2>
      <div v-if="typeSlices.length" class="distribution">
        <svg viewBox="0 0 160 160" aria-label="告警类型占比" role="img">
          <circle
            cx="80"
            cy="80"
            r="60"
            fill="none"
            stroke="var(--vision-chart-axis, #d7e0e8)"
            stroke-width="22"
          />
          <circle
            v-for="s in typeSlices"
            :key="s.eventId"
            cx="80"
            cy="80"
            r="60"
            fill="none"
            :stroke="s.color"
            stroke-width="22"
            pathLength="100"
            :stroke-dasharray="`${s.size} ${100 - s.size}`"
            :stroke-dashoffset="-s.offset"
            transform="rotate(-90 80 80)"
            @click="openAlerts({ eventId: s.eventId })"
          >
            <title>{{ s.eventName }}：{{ s.count }}</title>
          </circle>
          <text x="80" y="85" text-anchor="middle" class="pie-center">
            {{ data?.summary?.totalEvents || 0 }} 次
          </text>
        </svg>
        <div class="legend">
          <button
            v-for="s in typeSlices"
            :key="s.eventId"
            type="button"
            @click="openAlerts({ eventId: s.eventId })"
          >
            <i :style="{ background: s.color }" />{{ s.eventName
            }}<b>{{ s.count }} · {{ s.size.toFixed(1) }}%</b>
          </button>
        </div>
      </div>
      <el-empty v-else description="当前周期暂无告警" :image-size="72" />
    </article>
  </section>
</template>

<style scoped>
.overview {
  padding: 20px 24px 40px;
  color: var(--vision-text);
}
.heading,
.actions {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}
.kicker {
  display: inline-block;
  margin-bottom: 6px;
  color: var(--vision-text-secondary);
  font-size: 12px;
}
.heading h1,
.panel h2 {
  margin: 0 0 6px;
}
.heading p,
.meta {
  margin: 0;
  color: var(--vision-text-secondary);
  font-size: 13px;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin: 18px 0 10px;
}
@media (max-width: 1100px) {
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
.metric {
  text-align: left;
  border: 1px solid var(--vision-border);
  background: var(--vision-surface);
  border-radius: 12px;
  padding: 16px;
  cursor: pointer;
  color: inherit;
  display: grid;
  gap: 6px;
}
.metric strong {
  font-size: 28px;
  line-height: 1.1;
}
.metric strong.text {
  font-size: 22px;
}
.metric.danger strong {
  color: #dc2626;
}
.metric small {
  color: var(--vision-text-secondary);
}
.chart-panel {
  margin-top: 16px;
}
.panel {
  border: 1px solid var(--vision-border);
  background: var(--vision-surface);
  border-radius: 12px;
  padding: 16px;
}
.distribution {
  display: grid;
  grid-template-columns: 160px 1fr;
  gap: 16px;
  align-items: center;
}
.pie-center {
  fill: var(--vision-text);
  font-size: 14px;
}
.legend {
  display: grid;
  gap: 8px;
}
.legend button {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 0;
  background: transparent;
  padding: 0;
  cursor: pointer;
  color: inherit;
  text-align: left;
}
.legend i {
  width: 10px;
  height: 10px;
  border-radius: 50%;
}
.legend b {
  margin-left: auto;
  font-weight: 600;
}
@media (max-width: 1100px) {
  .metrics,
  .distribution {
    grid-template-columns: 1fr;
  }
}
</style>
