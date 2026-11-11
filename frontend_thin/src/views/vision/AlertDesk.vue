<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { request } from "@/api/vision";
import EvidenceClues from "@/components/vision/EvidenceClues.vue";
import { useUserStoreHook } from "@/store/modules/user";
import {
  alertQuery,
  dateText,
  dispositionLabels,
  riskLabels
} from "@/components/vision/alert-utils.mjs";

const muted = ref(false);
const busy = ref(false);
const error = ref("");
const note = ref("");
const summary = ref<any>({ pendingCount: 0, highestRisk: "" });
const queue = ref<any[]>([]);
const contacts = ref<any[]>([]);
const cameraLocations = ref<Record<string, string>>({});
const currentId = ref("");
const showPopup = ref(false);
let timer: ReturnType<typeof setInterval>;
let audio: HTMLAudioElement | null = null;

const current = computed(
  () => queue.value.find(row => row.id === currentId.value) || queue.value[0] || null
);

const currentPlace = computed(() => {
  const row = current.value;
  if (!row) return "—";
  if (row.location) return row.location;
  return cameraLocations.value[row.cameraId || ""] || "—";
});

function phoneHref(phone: unknown) {
  return `tel:${String(phone || "").replace(/\s+/g, "")}`;
}

function beep() {
  if (muted.value || typeof window === "undefined") return;
  try {
    if (!audio) {
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
      return;
    }
    audio.currentTime = 0;
    void audio.play();
  } catch {
    /* 忽略自动播放限制 */
  }
}

async function loadSummary() {
  const next = await request("/api/alerts/pending-summary");
  const prev = summary.value.pendingCount || 0;
  summary.value = next;
  if (next.pendingCount > 0) {
    showPopup.value = true;
    if (next.pendingCount > prev) beep();
  } else {
    showPopup.value = false;
  }
  return { prev, next };
}

async function loadContacts() {
  try {
    const result = await request("/api/contacts");
    contacts.value = result.contacts || [];
  } catch {
    contacts.value = [];
  }
}

async function loadCameraLocations() {
  try {
    const state = await request("/api/state");
    const map: Record<string, string> = {};
    for (const cam of state.cameras || []) {
      if (cam?.id) {
        const loc = (cam.location || "").trim();
        if (loc) map[cam.id] = loc;
      }
    }
    cameraLocations.value = map;
  } catch {
    cameraLocations.value = {};
  }
}

async function loadQueue(opts: { silent?: boolean } = {}) {
  if (!opts.silent) busy.value = true;
  error.value = "";
  try {
    const params = alertQuery({
      preset: "last30d",
      disposition: "pending",
      sort: "risk"
    });
    params.set("page", "1");
    params.set("pageSize", "50");
    const result = await request("/api/alerts/query?" + params);
    queue.value = result.rows || [];
    if (!queue.value.some(row => row.id === currentId.value)) {
      currentId.value = queue.value[0]?.id || "";
    }
    await loadSummary();
  } catch (e: any) {
    error.value = e.message || "加载未处置告警失败";
  } finally {
    if (!opts.silent) busy.value = false;
  }
}

async function dispose(kind: "handled" | "false_positive") {
  if (!current.value) return;
  busy.value = true;
  const action = kind === "handled" ? "处置完成" : "误报";
  try {
    const user = useUserStoreHook();
    await request("/api/alerts/dispose", {
      id: current.value.id,
      disposition: kind,
      handledBy: user.username || user.nickname || "值班员",
      note: note.value.trim()
    });
    note.value = "";
    await loadQueue();
    // 以 pending-summary 总数为准；queue 只是前 50 条，不能当剩余数
    const remaining = Number(summary.value.pendingCount) || 0;
    if (remaining === 0) {
      ElMessage.success(`已标记${action} · 全部处置完毕`);
    } else {
      ElMessage.success(
        `已标记${action} · 还有 ${remaining} 条待处理，已切换下一条`
      );
    }
  } catch (e: any) {
    ElMessage.error(e.message || "处置失败");
  } finally {
    busy.value = false;
  }
}

onMounted(() => {
  void loadContacts();
  void loadCameraLocations();
  void loadQueue();
  timer = setInterval(() => {
    void loadQueue({ silent: true });
  }, 10000);
});
onBeforeUnmount(() => clearInterval(timer));
</script>

<template>
  <section class="desk">
    <header class="desk-head">
      <div>
        <h1>告警处置</h1>
        <p>按风险从高到低、同风险最新优先 · 约 10 秒自动刷新 · 图片/视频线索</p>
      </div>
      <div class="actions">
        <el-switch v-model="muted" inline-prompt active-text="静音" inactive-text="提示音" />
        <el-button :loading="busy" @click="loadQueue">刷新</el-button>
        <el-button @click="$router.push('/alerts')">告警查询</el-button>
      </div>
    </header>

    <el-alert
      v-if="showPopup && summary.pendingCount > 0"
      class="popup"
      type="warning"
      show-icon
      :closable="false"
      :title="`未处置告警 ${summary.pendingCount} 条 · 最高风险 ${riskLabels[summary.highestRisk] || summary.highestRisk || '—'}`"
    />
    <el-alert v-if="error" type="error" :closable="false" :title="error" />

    <el-empty v-if="!busy && !queue.length" description="暂无未处置告警" />

    <div v-else class="desk-body">
      <aside class="list">
        <button
          v-for="row in queue"
          :key="row.id"
          type="button"
          class="item"
          :class="{ active: row.id === current?.id }"
          @click="currentId = row.id"
        >
          <strong>{{ row.eventName }}</strong>
          <span>{{ row.cameraName || row.cameraId }}</span>
          <span
            >风险 {{ riskLabels[row.riskLevel] || row.riskLevel }} ·
            {{ dateText(row.at) }}</span
          >
        </button>
      </aside>

      <article v-if="current" class="detail">
        <header>
          <h2>{{ current.eventName }}</h2>
          <dl class="meta-grid">
            <div>
              <dt>风险类型</dt>
              <dd>{{ riskLabels[current.riskLevel] || current.riskLevel || "—" }}</dd>
            </div>
            <div>
              <dt>发生时间</dt>
              <dd>{{ dateText(current.at) }}</dd>
            </div>
            <div>
              <dt>地点</dt>
              <dd>{{ currentPlace }}</dd>
            </div>
            <div>
              <dt>处置状态</dt>
              <dd>{{ dispositionLabels[current.disposition] || "未处置" }}</dd>
            </div>
          </dl>
        </header>
        <EvidenceClues
          :record="current"
          :regions="current.regions || []"
        />
        <section class="contacts">
          <h3>紧急联系人</h3>
          <ul v-if="contacts.length" class="contact-list">
            <li v-for="row in contacts" :key="row.id || row.name + row.phone">
              <strong>{{ row.name || "未命名" }}</strong>
              <a
                v-if="row.phone"
                class="phone"
                :href="phoneHref(row.phone)"
                >{{ row.phone }}</a
              >
              <span v-else class="muted">无手机号</span>
              <span v-if="row.org" class="muted">{{ row.org }}</span>
            </li>
          </ul>
          <p v-else class="muted">暂无紧急联系人，可在右上角头像「系统设置 → 紧急联系人」中维护</p>
        </section>
        <el-input
          v-model="note"
          type="textarea"
          :rows="2"
          maxlength="200"
          show-word-limit
          placeholder="可选：填写核实说明或处置备注"
        />
        <footer class="ops">
          <el-button type="primary" :disabled="busy" @click="dispose('handled')"
            >处置完成</el-button
          >
          <el-button :disabled="busy" @click="dispose('false_positive')"
            >误报</el-button
          >
        </footer>
      </article>
    </div>
  </section>
</template>

<style scoped>
.desk {
  padding: 20px 24px 40px;
  color: var(--vision-text);
}
.desk-head,
.actions,
.ops {
  display: flex;
  gap: 12px;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
}
.desk-head h1,
.detail h2,
.contacts h3 {
  margin: 0 0 6px;
}
.desk-head p,
.item span,
.muted {
  margin: 0;
  color: var(--vision-text-secondary);
  font-size: 13px;
}
.popup {
  margin: 12px 0;
}
.desk-body {
  display: grid;
  grid-template-columns: minmax(240px, 320px) 1fr;
  gap: 16px;
  margin-top: 16px;
}
.list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 70vh;
  overflow: auto;
}
.item {
  text-align: left;
  border: 1px solid var(--vision-border);
  background: var(--vision-surface);
  border-radius: 10px;
  padding: 12px;
  display: grid;
  gap: 4px;
  cursor: pointer;
  color: inherit;
}
.item.active {
  border-color: var(--vision-accent);
  box-shadow: inset 3px 0 0 var(--vision-accent);
}
.detail {
  border: 1px solid var(--vision-border);
  background: var(--vision-surface);
  border-radius: 12px;
  padding: 16px;
  display: grid;
  gap: 16px;
}
.meta-grid {
  margin: 8px 0 0;
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 8px 16px;
}
.meta-grid dt {
  margin: 0;
  color: var(--vision-text-secondary);
  font-size: 12px;
}
.meta-grid dd {
  margin: 2px 0 0;
  font-size: 14px;
}
.contacts {
  border-top: 1px solid var(--vision-border);
  padding-top: 12px;
}
.contact-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: grid;
  gap: 8px;
}
.contact-list li {
  display: flex;
  flex-wrap: wrap;
  gap: 8px 12px;
  align-items: baseline;
  font-size: 14px;
}
.phone {
  color: var(--vision-accent, #0d9488);
  text-decoration: none;
}
.ops {
  justify-content: flex-start;
}
@media (max-width: 960px) {
  .desk-body,
  .meta-grid {
    grid-template-columns: 1fr;
  }
}
</style>
