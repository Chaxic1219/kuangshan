<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { request } from "@/api/vision";
import { useVisionStore } from "@/stores/vision";
import AlertFilters from "@/components/vision/AlertFilters.vue";
import EvidenceClues from "@/components/vision/EvidenceClues.vue";
import {
  alertQuery,
  dateText,
  dispositionLabels,
  riskLabels
} from "@/components/vision/alert-utils.mjs";

const route = useRoute();
const store = useVisionStore();
const filters = ref<any>({
  preset: "today",
  from: "",
  to: "",
  cameraId: "",
  location: "",
  eventId: "",
  disposition: "",
  riskLevel: ""
});

const data = ref<any>(null);
const range = ref<any>(null);
const options = ref<any>(null);
const contacts = ref<any[]>([]);
const page = ref(1);
const busy = ref(false);
const error = ref("");
const evidence = ref<any>(null);
const evidenceOpen = ref(false);
let sequence = 0;
let mounted = false;
let controller: AbortController;
const cameras = computed(() => {
  const fromOptions = options.value?.filterCameras;
  if (fromOptions?.length) {
    return fromOptions.map((c: any) => ({
      ...c,
      location:
        c.location ||
        store.state?.cameras?.find((x: any) => x.id === c.cameraId)?.location ||
        ""
    }));
  }
  return (store.state?.cameras || []).map((c: any) => ({
    cameraId: c.id,
    cameraName: c.name,
    location: c.location || ""
  }));
});
const locations = computed(() => {
  const set = new Set<string>();
  for (const c of cameras.value || []) {
    const loc = String(c.location || "").trim();
    if (loc) set.add(loc);
  }
  for (const c of store.state?.cameras || []) {
    const loc = String(c.location || "").trim();
    if (loc) set.add(loc);
  }
  return [...set].sort((a, b) => a.localeCompare(b, "zh-CN"));
});
const events = computed(
  () =>
    options.value?.filterEvents ||
    Object.entries(store.state?.events || {})
      .filter(([, e]: any) => e.alarm)
      .map(([id, e]: any) => ({ eventId: id, eventName: e.name }))
);
const evidencePlace = computed(() => {
  const row = evidence.value;
  if (!row) return "—";
  if (row.location) return row.location;
  const cam = cameras.value.find((c: any) => c.cameraId === row.cameraId);
  return cam?.location || "—";
});
const exportUrl = computed(() =>
  data.value
    ? "/api/alerts/export.csv?" +
      alertQuery(data.value.filters, data.value.range)
    : ""
);

async function load(refreshOptions = false) {
  controller?.abort();
  const id = ++sequence;
  if (!mounted || document.hidden) return;
  controller = new AbortController();
  busy.value = true;
  try {
    const params = alertQuery(filters.value, range.value);
    params.set("page", String(page.value));
    params.set("pageSize", "20");
    const optionRequest = refreshOptions
      ? request(
          "/api/alerts/dashboard?" + alertQuery(filters.value, range.value),
          undefined,
          controller.signal
        ).catch(() => null)
      : Promise.resolve(null);
    const [result, nextOptions] = await Promise.all([
      request("/api/alerts/query?" + params, undefined, controller.signal),
      optionRequest
    ]);
    if (id !== sequence || !mounted) return;
    data.value = result;
    range.value = result.range;
    page.value = result.page;
    if (nextOptions) options.value = nextOptions;
    error.value = "";
  } catch (e) {
    if (id === sequence && mounted) error.value = e.message || "事件查询失败";
  } finally {
    if (id === sequence && mounted) busy.value = false;
  }
}
function apply(value) {
  filters.value = value;
  range.value = null;
  page.value = 1;
  load(true);
}
function routeFilters() {
  const get = (key, fallback = "") =>
    typeof route.query[key] === "string" ? route.query[key] : fallback;
  apply({
    preset: get("preset", "today"),
    from: get("from"),
    to: get("to"),
    cameraId: get("cameraId"),
    location: get("location"),
    eventId: get("eventId"),
    disposition: get("disposition"),
    riskLevel: get("riskLevel")
  });
}

async function loadContacts() {
  try {
    const result = await request("/api/contacts");
    contacts.value = result.contacts || [];
  } catch {
    contacts.value = [];
  }
}

function phoneHref(phone: unknown) {
  return `tel:${String(phone || "").replace(/\s+/g, "")}`;
}

function changePage(value) {
  page.value = value;
  load();
}
function refresh() {
  range.value = null;
  page.value = 1;
  load(true);
}
const evidenceIndex = computed(() => {
  const rows = data.value?.rows || [];
  if (!evidence.value) return -1;
  return rows.findIndex((row: any) => row.id === evidence.value.id);
});
function showEvidence(row) {
  evidence.value = row;
  evidenceOpen.value = true;
}
function shiftEvidence(delta: number) {
  const rows = data.value?.rows || [];
  const next = evidenceIndex.value + delta;
  if (next < 0 || next >= rows.length) return;
  evidence.value = rows[next];
}
function visibility() {
  controller?.abort();
  sequence++;
  busy.value = false;
  if (!document.hidden) load(!options.value);
}
watch(
  () => route.query,
  () => {
    if (mounted) routeFilters();
  }
);
onMounted(() => {
  mounted = true;
  document.addEventListener("visibilitychange", visibility);
  void loadContacts();
  routeFilters();
});
onBeforeUnmount(() => {
  mounted = false;
  sequence++;
  controller?.abort();
  document.removeEventListener("visibilitychange", visibility);
});
</script>

<template>
  <section class="alerts-page">
    <header>
      <div>
        <h1>告警查询</h1>
        <p>按位置、状态、级别与时间查询 · UTC+8 · 每页 20 条</p>
      </div>
      <div class="actions">
        <el-button type="primary" @click="$router.push('/alert-desk')"
          >进入处置台</el-button
        >
        <el-button :loading="busy" @click="refresh">刷新范围</el-button
        ><a v-if="data" :href="exportUrl" download="alerts.csv"
          >导出当前结果 CSV</a
        >
      </div>
    </header>
    <AlertFilters
      :model-value="filters"
      :range="range || data?.range"
      :cameras="cameras"
      :locations="locations"
      :events="events"
      @apply="apply"
    />
    <el-alert
      v-if="error"
      type="error"
      :closable="false"
      show-icon
      :title="'数据未更新：' + error"
    />
    <p v-if="data" class="meta">
      {{ error ? "保留上次成功结果" : "查询成功" }} ·
      {{ dateText(data.generatedAt) }} · 实际范围 [{{
        dateText(data.range.from)
      }}, {{ dateText(data.range.to) }})<br />结果筛选：{{
        data.filters.cameraId || "全部摄像头"
      }}
      ·
      {{
        events.find(e => e.eventId === data.filters.eventId)?.eventName ||
        data.filters.eventId ||
        "全部类型"
      }}
      ·
      {{ data.filters.location || "全部位置" }}。分页及导出使用同一实际范围。
    </p>
    <el-alert
      v-if="data?.excludedRecords"
      :title="`${data.excludedRecords} 条记录无效或损坏，已排除`"
      type="warning"
      :closable="false"
    />
    <el-empty
      v-if="!data"
      :description="busy ? '正在查询事件…' : '暂无可用结果，请重试'"
    />
    <template v-else>
      <el-table
        :data="data.rows"
        empty-text="当前筛选范围暂无告警记录"
        row-key="id"
      >
        <el-table-column label="发生时间" min-width="180"
          ><template #default="{ row }">{{
            dateText(row.at)
          }}</template></el-table-column
        >
        <el-table-column prop="cameraName" label="摄像头" min-width="140" />
        <el-table-column label="地点" min-width="120"
          ><template #default="{ row }">{{
            row.location ||
            cameras.find((c: any) => c.cameraId === row.cameraId)?.location ||
            "—"
          }}</template></el-table-column
        >
        <el-table-column prop="eventName" label="事件类型" min-width="140" />
        <el-table-column label="风险" width="80"
          ><template #default="{ row }">{{
            riskLabels[row.riskLevel] || row.riskLevel || "—"
          }}</template></el-table-column
        >
        <el-table-column label="处置状态" width="100"
          ><template #default="{ row }">{{
            dispositionLabels[row.disposition] || "未处置"
          }}</template></el-table-column
        >
        <el-table-column prop="detectionCount" label="检测目标数" width="110" />
        <el-table-column label="证据" width="110"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="showEvidence(row)"
              >查看证据</el-button
            ></template
          ></el-table-column
        >
      </el-table>
      <div class="pagination">
        <span>共 {{ data.total }} 条事件 · 当前展示第 {{ data.page }} 页</span
        ><el-pagination
          :current-page="data.page"
          :page-size="20"
          :total="data.total"
          layout="prev, pager, next"
          :disabled="busy || !!error"
          @current-change="changePage"
        />
      </div>
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
        ><dl class="evidence-meta">
          <div>
            <dt>风险类型</dt>
            <dd>
              {{ riskLabels[evidence.riskLevel] || evidence.riskLevel || "—" }}
            </dd>
          </div>
          <div>
            <dt>发生时间</dt>
            <dd>{{ dateText(evidence.at) }}</dd>
          </div>
          <div>
            <dt>地点</dt>
            <dd>{{ evidencePlace }}</dd>
          </div>
          <div>
            <dt>处置状态</dt>
            <dd>
              {{
                dispositionLabels[evidence.disposition] || "未处置"
              }}
            </dd>
          </div>
        </dl>
        <EvidenceClues :record="evidence" />
        <section class="evidence-contacts">
          <h3>紧急联系人</h3>
          <ul v-if="contacts.length">
            <li v-for="row in contacts" :key="row.id || row.name + row.phone">
              <strong>{{ row.name || "未命名" }}</strong>
              <a
                v-if="row.phone"
                class="phone"
                :href="phoneHref(row.phone)"
                >{{ row.phone }}</a
              >
              <span v-else class="muted">无手机号</span>
            </li>
          </ul>
          <p v-else class="muted">暂无紧急联系人，可在右上角头像「系统设置 → 紧急联系人」中维护</p>
        </section>
        <p class="muted">检测框与图片均来自此条历史记录。</p>
        <div class="evidence-nav">
          <el-button
            :disabled="evidenceIndex <= 0"
            @click="shiftEvidence(-1)"
            >上一条</el-button
          >
          <span
            >{{ evidenceIndex + 1 }} /
            {{ data?.rows?.length || 0 }}（当前页）</span
          >
          <el-button
            :disabled="
              evidenceIndex < 0 || evidenceIndex >= (data?.rows?.length || 0) - 1
            "
            @click="shiftEvidence(1)"
            >下一条</el-button
          >
        </div></template
      ></el-dialog
    >
  </section>
</template>

<style scoped>
.evidence-nav {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-top: 12px;
}
.evidence-meta {
  margin: 0 0 12px;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 16px;
}
.evidence-meta dt {
  margin: 0;
  color: var(--vision-text-secondary);
  font-size: 12px;
}
.evidence-meta dd {
  margin: 2px 0 0;
  font-size: 14px;
}
.evidence-contacts {
  margin: 12px 0;
  padding-top: 10px;
  border-top: 1px solid var(--vision-border);
}
.evidence-contacts h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
.evidence-contacts ul {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 6px;
}
.evidence-contacts li {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
  align-items: baseline;
}
.phone {
  color: var(--vision-accent, #0d9488);
  text-decoration: none;
}
.muted {
  color: var(--vision-text-secondary);
  font-size: 13px;
}
.alerts-page {
  padding: 8px 4px 22px;
  background: transparent;
  color: var(--vision-text);
  min-height: calc(100vh - 130px);
}
header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}
h1 {
  font-size: 24px;
  margin: 0;
  color: var(--vision-text);
  font-weight: 650;
}
header p,
.meta {
  color: var(--vision-text-secondary);
  font-size: 12px;
  line-height: 1.8;
}
.actions {
  display: flex;
  align-items: center;
  gap: 16px;
}
.actions a {
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
.actions a:hover {
  background: var(--vision-accent-hover);
  color: var(--el-color-white, #fff);
}
.pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  margin-top: 20px;
  padding: 14px 16px;
  background: var(--vision-surface);
  border: 1px solid var(--vision-border);
  border-radius: 10px;
}
.pagination span {
  color: var(--vision-text-secondary);
  font-size: 13px;
}
.el-alert {
  margin: 12px 0;
}
</style>
