<script setup lang="ts">
import { computed, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import { useVisionStore } from "@/stores/vision";
import { request } from "@/api/vision";
import { statusText, statusType } from "./cameraStatus";

const vision = useVisionStore();
const busy = ref("");

const capacity = computed(() => vision.state.capacity || {});
const storage = computed(() => capacity.value.storage || {});
const cameras = computed(() => vision.state.cameras || []);
const diskBlocked = computed(() => storage.value.level === "blocked");

function eventName(eventId: string) {
  return vision.state.events?.[eventId]?.name || eventId;
}

function enabledTasks(camera: any) {
  return (camera.tasks || []).filter((t: any) => t.enabled);
}

function modelErrorText(camera: any) {
  const errors = camera.modelErrors || {};
  const keys = Object.keys(errors);
  if (!keys.length) return "";
  return keys.map(k => `${eventName(k)}: ${errors[k]}`).join("；");
}

function resourceText(camera: any) {
  const r = camera.resource || {};
  if (!camera.running || !r.available) return "—";
  const cpu = r.cpuPercent == null ? "—" : `${Number(r.cpuPercent).toFixed(1)}%`;
  const rss = r.rssMb == null ? "—" : `${Number(r.rssMb).toFixed(0)} MB`;
  return `${cpu} / ${rss}`;
}

function needsForceRestart(camera: any) {
  return (
    !!camera.running ||
    ["starting", "active", "backoff", "stopping"].includes(camera.lifecycle)
  );
}

function actionLabel(camera: any) {
  return needsForceRestart(camera) ? "强制重启" : "启动";
}

async function act(camera: any) {
  if (busy.value || diskBlocked.value) return;
  const force = needsForceRestart(camera);
  const path = force ? "/api/cameras/restart" : "/api/cameras/start";
  if (force) {
    try {
      await ElMessageBox.confirm(
        `强制重启「${camera.name}」？将先停止当前分析进程，再按已保存配置重新启动。`,
        "强制重启",
        { type: "warning", confirmButtonText: "重启", cancelButtonText: "取消" }
      );
    } catch {
      return;
    }
  }
  busy.value = camera.id;
  try {
    await request(path, { id: camera.id });
    ElMessage.success(force ? "已提交强制重启" : "已提交启动");
  } catch (e: any) {
    ElMessage.error(e.message || "操作失败");
  } finally {
    await vision.refresh();
    busy.value = "";
  }
}

function formatNum(value: unknown, suffix = "") {
  if (value == null || Number.isNaN(Number(value))) return "—";
  return `${value}${suffix}`;
}

async function clearBacklog() {
  if (busy.value) return;
  try {
    await ElMessageBox.confirm(
      "将把「超过 24 小时」的未处置告警标记为已处置（备注：历史未处置积压清理），近 24 小时内未处置告警会保留。是否继续？",
      "清理历史未处置",
      { type: "warning", confirmButtonText: "清理", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  busy.value = "bulk";
  try {
    const result = await request("/api/alerts/bulk-dispose", {
      keepWithinHours: 24,
      disposition: "handled",
      handledBy: "系统清理",
      note: "历史未处置积压清理"
    });
    ElMessage.success(
      `已清理 ${result.updated || 0} 条，保留近窗 ${result.keptRecent || 0} 条`
    );
  } catch (e: any) {
    ElMessage.error(e.message || "清理失败");
  } finally {
    busy.value = "";
  }
}

async function backfillVideos() {
  if (busy.value) return;
  try {
    await ElMessageBox.confirm(
      "将为仍只有图片、没有视频线索的历史告警生成静止短视频（由证据图合成）。可能耗时较长，是否继续？",
      "回填视频线索",
      { type: "info", confirmButtonText: "开始回填", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  busy.value = "backfill";
  try {
    const result = await request("/api/alerts/backfill-videos", { limit: 0 });
    ElMessage.success(
      `扫描 ${result.scanned || 0} · 新生成 ${result.created || 0} · 跳过 ${result.skipped || 0}`
    );
  } catch (e: any) {
    ElMessage.error(e.message || "回填失败");
  } finally {
    busy.value = "";
  }
}
</script>

<template>
  <div class="vision-page">
    <header class="vision-heading">
      <div>
        <span class="vision-kicker">运维中心 / 进程与资源</span>
        <h1>系统运维</h1>
        <p>
          查看每路分析进程与模型任务状态。已停止/失败可启动；运行中可强制重启。关注进程是否存活，而非是否产生告警。
        </p>
      </div>
      <el-button :disabled="!vision.connected" @click="vision.refresh()">刷新状态</el-button>
    </header>

    <el-alert
      v-if="!vision.connected"
      class="vision-error"
      :title="vision.error || '正在连接本地服务…'"
      type="warning"
      :closable="false"
      show-icon
    />
    <el-alert
      v-if="diskBlocked"
      class="vision-error"
      title="磁盘剩余不足 1 GiB，已禁止启动与重启，请先清理空间"
      type="error"
      :closable="false"
      show-icon
    />

    <div class="vision-summary ops-summary">
      <article>
        <span>运行路数</span>
        <strong
          >{{ formatNum(capacity.running) }}/{{
            formatNum(capacity.maxRunning)
          }}</strong
        >
      </article>
      <article>
        <span>分析进程 CPU</span>
        <strong>{{ formatNum(capacity.cpuPercent, "%") }}</strong>
      </article>
      <article>
        <span>分析进程内存</span>
        <strong>{{ formatNum(capacity.rssMb, " MB") }}</strong>
      </article>
      <article>
        <span>磁盘剩余</span>
        <strong>
          <template v-if="storage.freeGiB != null"
            >{{ Number(storage.freeGiB).toFixed(1) }} GiB</template
          >
          <template v-else>—</template>
        </strong>
        <small class="ops-sub">{{ storage.level || "unknown" }}</small>
      </article>
      <article class="ops-gpu">
        <span>GPU</span>
        <strong>暂未接入</strong>
        <small class="ops-sub">第一期仅 CPU / 内存 / 磁盘</small>
      </article>
    </div>

    <div class="vision-panel">
      <h2>摄像头进程</h2>
      <el-table :data="cameras" empty-text="暂无视频配置" row-key="id">
        <el-table-column prop="name" label="视频" min-width="140" />
        <el-table-column label="状态" width="120">
          <template #default="{ row }">
            <el-tag :type="statusType(row)" size="small">{{
              statusText(row)
            }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column label="启用任务" min-width="180">
          <template #default="{ row }">
            <template v-if="enabledTasks(row).length">
              <el-tag
                v-for="t in enabledTasks(row)"
                :key="t.eventId"
                size="small"
                class="ops-task"
                >{{ eventName(t.eventId) }}</el-tag
              >
            </template>
            <span v-else class="vision-muted">无</span>
          </template>
        </el-table-column>
        <el-table-column label="CPU / 内存" width="130">
          <template #default="{ row }">{{ resourceText(row) }}</template>
        </el-table-column>
        <el-table-column label="模型异常" min-width="160">
          <template #default="{ row }">
            <span :class="modelErrorText(row) ? 'ops-warn' : 'vision-muted'">{{
              modelErrorText(row) || "—"
            }}</span>
          </template>
        </el-table-column>
        <el-table-column label="失败 / 重启" min-width="160">
          <template #default="{ row }">
            <div class="ops-meta">
              <span>{{ row.lastFailure || "—" }}</span>
              <span class="vision-muted"
                >重启 {{ row.restartCount || 0
                }}<template v-if="row.nextRestartIn != null"
                  > · {{ row.nextRestartIn }}s 后</template
                ></span
              >
            </div>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button
              type="primary"
              link
              :disabled="!vision.connected || diskBlocked || !!busy"
              :loading="busy === row.id"
              @click="act(row)"
              >{{ actionLabel(row) }}</el-button
            >
          </template>
        </el-table-column>
      </el-table>
      <p class="vision-muted" style="margin-top: 14px">
        看门狗 {{ capacity.watchdogSeconds || "—" }}s · 初始化超时
        {{ capacity.initialTimeoutSeconds || "—" }}s · 采样范围
        {{ capacity.cpuScope || "process" }}
      </p>
    </div>

    <div class="vision-panel ops-maintain">
      <h2>告警库维护</h2>
      <p class="vision-muted">
        清理历史未处置积压（默认保留近 24 小时），并为仅有图片的旧告警补静止短视频线索。需管理员会话。
      </p>
      <div class="ops-maintain-actions">
        <el-button
          type="warning"
          :disabled="!vision.connected || !!busy"
          :loading="busy === 'bulk'"
          @click="clearBacklog"
          >清理历史未处置</el-button
        >
        <el-button
          :disabled="!vision.connected || !!busy"
          :loading="busy === 'backfill'"
          @click="backfillVideos"
          >回填缺失视频线索</el-button
        >
      </div>
    </div>
  </div>
</template>

<style scoped>
.ops-summary {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}
.ops-summary small.ops-sub {
  display: block;
  margin-top: 6px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}
.ops-gpu strong {
  font-size: 18px;
}
.ops-task {
  margin: 0 4px 4px 0;
}
.ops-warn {
  color: var(--vision-warning);
}
.ops-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  line-height: 1.4;
}
.ops-maintain {
  margin-top: 16px;
}
.ops-maintain-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin-top: 12px;
}
@media (max-width: 1100px) {
  .ops-summary {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
