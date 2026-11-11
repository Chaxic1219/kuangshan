<script setup lang="ts">
import { computed, onMounted, onBeforeUnmount, ref } from "vue";
import { useRouter } from "vue-router";
import { request } from "@/api/vision";
import AlertFilters from "@/components/vision/AlertFilters.vue";
import DetectionFrame from "@/components/vision/DetectionFrame.vue";
import {
  alertQuery,
  bucketRange,
  dateText,
  trendPoints,
  stateLabels
} from "@/components/vision/alert-utils.mjs";

const router = useRouter();
const shell = ref<HTMLElement>();
const filters = ref({
  preset: "today",
  from: "",
  to: "",
  cameraId: "",
  eventId: ""
});
const data = ref<any>(null);
const error = ref("");
const busy = ref(false);
const fullscreen = ref(false);
const fullscreenError = ref("");
const evidence = ref<any>(null);
const evidenceOpen = ref(false);
let controller: AbortController;
let sequence = 0;
let timer: ReturnType<typeof setTimeout>;
let mounted = false;
const colors = [
  "#d97706",
  "#0d9488",
  "#2563eb",
  "#dc2626",
  "#7c3aed",
  "#0891b2"
];
const points = computed(() => trendPoints(data.value?.trend || []));
const line = computed(() =>
  points.value.map((p, i) => `${i ? "L" : "M"}${p.x},${p.y}`).join(" ")
);
const typeSlices = computed(() => {
  let offset = 0;
  return (data.value?.byType || []).map((item, i) => {
    const size =
      (item.count / Math.max(1, data.value.summary.totalEvents)) * 100;
    const result = { ...item, size, offset, color: colors[i % colors.length] };
    offset += size;
    return result;
  });
});
const maxCamera = computed(() =>
  Math.max(1, ...(data.value?.byCamera || []).map(c => c.count))
);
const exportUrl = computed(() =>
  data.value
    ? "/api/alerts/export.csv?" +
      alertQuery(data.value.filters, data.value.range)
    : ""
);

async function load() {
  clearTimeout(timer);
  controller?.abort();
  const id = ++sequence;
  if (!mounted || document.hidden) return;
  controller = new AbortController();
  busy.value = true;
  try {
    const result = await request(
      "/api/alerts/dashboard?" + alertQuery(filters.value),
      undefined,
      controller.signal
    );
    if (id !== sequence || !mounted) return;
    data.value = result;
    error.value = "";
  } catch (e) {
    if (id === sequence && mounted) error.value = e.message || "统计请求失败";
  } finally {
    if (id === sequence && mounted) {
      busy.value = false;
      if (!document.hidden) timer = setTimeout(load, 10000);
    }
  }
}
function apply(value) {
  filters.value = value;
  load();
}
function visibility() {
  clearTimeout(timer);
  controller?.abort();
  sequence++;
  busy.value = false;
  if (!document.hidden) load();
}
function drill(extra = {}, range = data.value?.range) {
  if (!data.value || !range) return;
  router.push({
    path: "/alerts",
    query: Object.fromEntries(
      alertQuery({ ...data.value.filters, ...extra }, range)
    )
  });
}
function drillBucket(point) {
  drill({}, bucketRange(point.bucket, data.value.bucket, data.value.range));
}
function showEvidence(row) {
  evidence.value = row;
  evidenceOpen.value = true;
}
function fullscreenChanged() {
  fullscreen.value = document.fullscreenElement === shell.value;
}
async function toggleFullscreen() {
  try {
    if (fullscreen.value) await document.exitFullscreen();
    else await shell.value?.requestFullscreen();
    fullscreenError.value = "";
  } catch {
    fullscreenError.value = "浏览器未允许全屏，可继续使用普通页面。";
  }
}
onMounted(() => {
  mounted = true;
  document.addEventListener("visibilitychange", visibility);
  document.addEventListener("fullscreenchange", fullscreenChanged);
  load();
});
onBeforeUnmount(() => {
  mounted = false;
  sequence++;
  clearTimeout(timer);
  controller?.abort();
  document.removeEventListener("visibilitychange", visibility);
  document.removeEventListener("fullscreenchange", fullscreenChanged);
  if (document.fullscreenElement === shell.value)
    document.exitFullscreen().catch(() => {});
});
</script>

<template>
  <section ref="shell" class="dashboard">
    <header class="heading">
      <div>
        <h1>告警态势</h1>
        <p>真实事件统计 · UTC+8 · 每 10 秒更新</p>
      </div>
      <div class="actions">
        <el-button :loading="busy" @click="load">刷新</el-button
        ><a v-if="data" :href="exportUrl" download="alerts.csv" class="export"
          >导出 CSV</a
        ><el-button @click="toggleFullscreen">{{
          fullscreen ? "退出全屏" : "全屏展示"
        }}</el-button>
      </div>
    </header>
    <AlertFilters
      :model-value="filters"
      :range="data?.range"
      :cameras="data?.filterCameras"
      :events="data?.filterEvents"
      @apply="apply"
    />
    <el-alert
      v-if="error"
      type="error"
      :closable="false"
      :title="'数据未更新：' + error"
      show-icon
    />
    <el-alert
      v-if="fullscreenError"
      type="warning"
      :closable="false"
      :title="fullscreenError"
    />
    <p class="meta" v-if="data">
      {{ error ? "上次成功" : "已更新" }} {{ dateText(data.generatedAt) }} ·
      实际范围 [{{ dateText(data.range.from) }}, {{ dateText(data.range.to) }})
    </p>
    <el-empty
      v-if="!data"
      :description="busy ? '正在加载真实告警统计…' : '暂无可用统计，请重试'"
    />
    <template v-else>
      <div class="metrics">
        <button @click="drill()">
          <span>告警事件总数</span
          ><strong>{{ data.summary.totalEvents }}</strong
          ><small>查看全部明细</small>
        </button>
        <button @click="drill()">
          <span>发生告警路数</span
          ><strong>{{ data.summary.cameraCount }}</strong
          ><small>有告警的摄像头</small>
        </button>
        <button @click="drill()">
          <span>告警类型数</span><strong>{{ data.summary.typeCount }}</strong
          ><small>非零事件类型</small>
        </button>
        <button
          @click="
            data.summary.topType &&
            drill({ eventId: data.summary.topType.eventId })
          "
        >
          <span>最多告警类型</span
          ><strong class="text-value">{{
            data.summary.topType?.eventName || "—"
          }}</strong
          ><small>{{
            data.summary.topType
              ? `${data.summary.topType.count} 次${data.summary.topType.tied ? "（并列：" + data.summary.topType.tiedNames.join("、") + "）" : ""}`
              : "暂无告警"
          }}</small>
        </button>
        <button @click="data.recent[0] && showEvidence(data.recent[0])">
          <span>最近告警时间</span
          ><strong class="text-value">{{
            dateText(data.summary.latestAt)
          }}</strong
          ><small>查看最近证据</small>
        </button>
      </div>
      <el-alert
        v-for="hint in data.hints"
        :key="hint"
        :title="hint"
        type="info"
        :closable="false"
      />
      <el-alert
        v-if="data.excludedRecords"
        :title="`${data.excludedRecords} 条记录字段无效或损坏，未纳入统计`"
        type="warning"
        :closable="false"
      />
      <div class="chart-grid">
        <article class="panel">
          <h2>
            告警数量趋势
            <small
              >按{{ data.bucket === "hour" ? "小时" : "天" }} ·
              点击查看时段</small
            >
          </h2>
          <svg
            viewBox="0 0 640 200"
            role="img"
            aria-label="告警数量趋势"
            class="trend"
          >
            <path
              :d="line"
              fill="none"
              stroke="var(--vision-chart-line)"
              stroke-width="2.5"
            />
            <line
              x1="32"
              y1="170"
              x2="608"
              y2="170"
              stroke="var(--vision-chart-axis)"
            />
            <g v-for="(p, i) in points" :key="p.bucket">
              <circle
                :cx="p.x"
                :cy="p.y"
                r="5"
                fill="var(--vision-chart-line)"
                tabindex="0"
                role="button"
                :aria-label="`${p.bucket}，${p.count} 次，查看明细`"
                @click="drillBucket(p)"
                @keydown.enter="drillBucket(p)"
              >
                <title>{{ p.bucket }}：{{ p.count }}</title>
              </circle>
              <text
                v-if="
                  i === 0 ||
                  i === points.length - 1 ||
                  i % Math.ceil(points.length / 5) === 0
                "
                :x="p.x"
                y="192"
                text-anchor="middle"
              >
                {{ p.bucket.slice(5) }}
              </text>
            </g>
            <text x="32" y="18">
              峰值 {{ Math.max(0, ...data.trend.map(p => p.count)) }}
            </text>
          </svg>
          <p v-if="!data.summary.totalEvents" class="meta">
            当前范围无告警记录
          </p>
        </article>
        <article class="panel">
          <h2>告警类型分布</h2>
          <div v-if="typeSlices.length" class="distribution">
            <svg viewBox="0 0 160 160" aria-label="告警类型占比" role="img">
              <circle
                cx="80"
                cy="80"
                r="60"
                fill="none"
                stroke="var(--vision-chart-axis)"
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
                @click="drill({ eventId: s.eventId })"
              >
                <title>{{ s.eventName }}：{{ s.count }}</title>
              </circle>
              <text x="80" y="85" text-anchor="middle">
                {{ data.summary.totalEvents }} 次
              </text>
            </svg>
            <div class="legend">
              <button
                v-for="s in typeSlices"
                :key="s.eventId"
                @click="drill({ eventId: s.eventId })"
              >
                <i :style="{ background: s.color }" />{{ s.eventName
                }}<b>{{ s.count }} · {{ s.size.toFixed(1) }}%</b>
              </button>
            </div>
          </div>
          <el-empty v-else description="当前范围暂无告警" :image-size="70" />
        </article>
        <article class="panel">
          <h2>摄像头告警排行</h2>
          <svg
            v-if="data.byCamera.length"
            :viewBox="`0 0 640 ${data.byCamera.length * 40 + 10}`"
            role="img"
            aria-label="摄像头告警排行"
            class="bars"
          >
            <g
              v-for="(c, i) in data.byCamera"
              :key="c.cameraId"
              :transform="`translate(0,${i * 40})`"
              :role="c.remainder ? undefined : 'button'"
              :tabindex="c.remainder ? undefined : 0"
              :aria-label="`${c.cameraName}，${c.count} 次`"
              @click="!c.remainder && drill({ cameraId: c.cameraId })"
              @keydown.enter="!c.remainder && drill({ cameraId: c.cameraId })"
            >
              <title>
                {{ c.cameraName }}：{{ c.count
                }}{{ c.remainder ? "（合计，不支持单路筛选）" : "" }}
              </title>
              <text x="0" y="25">
                {{
                  c.cameraName.length > 12
                    ? c.cameraName.slice(0, 12) + "…"
                    : c.cameraName
                }}
              </text>
              <rect
                x="165"
                y="8"
                :width="(c.count / maxCamera) * 400"
                height="24"
                rx="3"
                :fill="
                  c.remainder
                    ? 'var(--vision-chart-muted)'
                    : 'var(--vision-chart-bar)'
                "
              />
              <text :x="175 + (c.count / maxCamera) * 400" y="25">
                {{ c.count }}
              </text>
            </g></svg
          ><el-empty v-else description="当前范围暂无告警" :image-size="70" />
        </article>
        <article class="panel recent">
          <div class="heading">
            <h2>最近 20 条告警</h2>
            <el-button link type="primary" @click="drill()">全部明细</el-button>
          </div>
          <el-table
            :data="data.recent"
            max-height="360"
            empty-text="当前范围暂无告警"
            ><el-table-column label="发生时间" min-width="160"
              ><template #default="{ row }">{{
                dateText(row.at)
              }}</template></el-table-column
            ><el-table-column
              prop="cameraName"
              label="摄像头"
              min-width="100"
            /><el-table-column
              prop="eventName"
              label="类型"
              min-width="100"
            /><el-table-column label="证据" width="84"
              ><template #default="{ row }"
                ><el-button link type="primary" @click="showEvidence(row)"
                  >查看</el-button
                ></template
              ></el-table-column
            ></el-table
          >
        </article>
      </div>
      <article class="panel">
        <h2>当前分析状态</h2>
        <div class="status-list">
          <el-tag
            v-for="c in data.analysis"
            :key="c.cameraId"
            :type="
              c.state === 'running'
                ? 'success'
                : ['failed', 'error'].includes(c.state)
                  ? 'danger'
                  : 'info'
            "
            >{{ c.cameraName }} · {{ stateLabels[c.state] || c.state }}</el-tag
          ><span v-if="!data.analysis.length">没有匹配的已配置摄像头</span>
        </div>
        <p class="meta">
          来自当前运行状态，不受历史时间范围影响。一条告警记录计一次事件；告警减少不等于安全改善。
        </p>
      </article>
    </template>
    <el-dialog
      v-model="evidenceOpen"
      title="告警证据"
      width="min(920px, 95vw)"
      append-to-body
      :close-on-press-escape="true"
      destroy-on-close
      align-center
      ><template v-if="evidence"
        ><p>
          {{ evidence.cameraName }} · {{ evidence.eventName }} ·
          {{ dateText(evidence.at) }}
        </p>
        <DetectionFrame :record="evidence" />
        <p>证据来自该次告警记录。</p></template
      ></el-dialog
    >
  </section>
</template>

<style scoped>
.dashboard {
  padding: 8px 4px 20px;
  background: transparent;
  color: var(--vision-text);
  min-height: 100%;
}
.dashboard:fullscreen {
  overflow: auto;
  border-radius: 0;
  padding: 24px;
  background: var(--vision-surface-page);
}
.heading {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.heading h1 {
  font-size: 24px;
  margin: 0;
  color: var(--vision-text);
  font-weight: 650;
}
.heading p,
.meta {
  font-size: 12px;
  color: var(--vision-text-secondary);
  margin: 8px 0 14px;
}
.actions {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
}
.export {
  display: inline-flex;
  align-items: center;
  height: 32px;
  padding: 0 14px;
  border-radius: 6px;
  background: var(--vision-accent);
  color: var(--el-color-white, #fff);
  font-size: 14px;
  text-decoration: none;
}
.export:hover {
  background: var(--vision-accent-hover);
  color: var(--el-color-white, #fff);
}
.metrics {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 14px;
  margin: 18px 0;
}
.metrics button,
.panel {
  background: var(--vision-surface);
  border: 1px solid var(--vision-border);
  border-radius: 10px;
  padding: 18px;
  box-shadow: none;
}
.metrics button {
  text-align: left;
  color: inherit;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: 10px;
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}
.metrics button:hover {
  border-color: var(--vision-accent-soft);
  box-shadow: 0 4px 12px color-mix(in srgb, var(--vision-text) 8%, transparent);
}
.metrics span,
.metrics small {
  color: var(--vision-text-secondary);
  font-size: 12px;
}
.metrics strong {
  font-size: 34px;
  color: var(--vision-warning);
  font-variant-numeric: tabular-nums;
  font-weight: 650;
}
.metrics .text-value {
  font-size: 17px;
  min-height: 41px;
  color: var(--vision-text);
}
.chart-grid {
  display: grid;
  grid-template-columns: 1.15fr 1fr;
  gap: 16px;
  margin: 16px 0;
}
.panel h2 {
  font-size: 15px;
  margin: 0 0 16px;
  color: var(--vision-text);
}
.panel h2 small {
  font-size: 11px;
  font-weight: normal;
  color: var(--vision-text-secondary);
}
.trend,
.bars {
  width: 100%;
}
svg text {
  fill: var(--vision-text-secondary);
  font-size: 11px;
}
svg [role="button"],
svg circle {
  cursor: pointer;
}
.distribution {
  display: flex;
  align-items: center;
  gap: 18px;
  min-height: 210px;
}
.distribution svg {
  width: 160px;
  flex-shrink: 0;
}
.legend {
  flex: 1;
}
.legend button {
  display: flex;
  align-items: center;
  gap: 8px;
  background: none;
  border: 0;
  color: var(--vision-text-regular);
  padding: 8px 0;
  width: 100%;
  text-align: left;
  cursor: pointer;
  font-size: 12px;
}
.legend button:hover {
  color: var(--vision-accent);
}
.legend b {
  margin-left: auto;
  color: var(--vision-text-secondary);
  font-variant-numeric: tabular-nums;
}
.legend i {
  width: 9px;
  height: 9px;
  border-radius: 3px;
}
.status-list {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.el-alert {
  margin-bottom: 10px;
}
@media (max-width: 1100px) {
  .metrics {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .chart-grid {
    grid-template-columns: 1fr;
  }
  .heading {
    flex-wrap: wrap;
  }
}
@media (max-width: 600px) {
  .metrics {
    grid-template-columns: 1fr 1fr;
  }
  .dashboard {
    padding: 4px 0 12px;
  }
  .distribution {
    flex-wrap: wrap;
  }
}
</style>
