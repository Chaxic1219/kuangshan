<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { request } from "@/api/vision";
import type { ListItem, TabItem } from "./data";
import NoticeList from "./components/NoticeList.vue";
import BellIcon from "~icons/ep/bell";

const router = useRouter();
const activeKey = ref("alerts");
const recent = ref<ListItem[]>([]);
const total = ref(0);
let timer: ReturnType<typeof setTimeout> | undefined;
let alive = false;

function formatTime(iso: string) {
  const stamp = Date.parse(iso);
  if (!Number.isFinite(stamp)) return iso || "";
  return new Date(stamp).toLocaleString("zh-CN", { hour12: false });
}

async function refresh() {
  clearTimeout(timer);
  try {
    const data = await request("/api/alerts/dashboard?preset=today");
    if (!alive) return;
    total.value = Number(data?.summary?.totalEvents || 0);
    recent.value = (data?.recent || []).slice(0, 12).map((row: any) => ({
      avatar: "",
      title: row.eventName || "告警事件",
      description: `${row.cameraName || row.cameraId || "未知视频"} · ${row.detectionCount || 0} 个目标`,
      datetime: formatTime(row.at || row.capturedAt || ""),
      type: "alerts",
      status: "warning" as const,
      extra: "告警",
      cameraId: row.cameraId,
      eventId: row.eventId,
      id: row.id
    }));
  } catch {
    /* 保留上次列表，避免网络抖动清空提醒 */
  } finally {
    if (alive) timer = setTimeout(refresh, 15000);
  }
}

const notices = computed<TabItem[]>(() => [
  {
    key: "alerts",
    name: "告警",
    list: recent.value,
    emptyText: "今日暂无告警"
  }
]);

const noticesNum = computed(() => total.value);

const getLabel = computed(
  () => (item: TabItem) =>
    item.name + (item.list.length > 0 ? `(${item.list.length})` : "")
);

function goDashboard() {
  router.push("/dashboard/index");
}

function goAlert(item: ListItem) {
  router.push({
    path: "/alerts/index",
    query: {
      preset: "today",
      ...(item.cameraId ? { cameraId: String(item.cameraId) } : {}),
      ...(item.eventId ? { eventId: String(item.eventId) } : {})
    }
  });
}

onMounted(() => {
  alive = true;
  void refresh();
});
onBeforeUnmount(() => {
  alive = false;
  clearTimeout(timer);
});
</script>

<template>
  <el-dropdown trigger="click" placement="bottom-end">
    <span
      :class="[
        'dropdown-badge',
        'navbar-bg-hover',
        'select-none',
        Number(noticesNum) !== 0 && 'mr-[10px]'
      ]"
    >
      <el-badge :value="Number(noticesNum) === 0 ? '' : noticesNum" :max="99">
        <span class="header-notice-icon">
          <IconifyIconOffline :icon="BellIcon" />
        </span>
      </el-badge>
    </span>
    <template #dropdown>
      <el-dropdown-menu>
        <el-tabs
          v-model="activeKey"
          :stretch="true"
          class="dropdown-tabs"
          :style="{ width: '330px' }"
        >
          <template v-for="item in notices" :key="item.key">
            <el-tab-pane :label="getLabel(item)" :name="`${item.key}`">
              <el-scrollbar max-height="330px">
                <div class="noticeList-container">
                  <NoticeList
                    :list="item.list"
                    :empty-text="item.emptyText"
                    @select="goAlert"
                  />
                </div>
              </el-scrollbar>
              <div class="notice-footer">
                <el-button link type="primary" @click="goDashboard()"
                  >进入告警大屏</el-button
                >
              </div>
            </el-tab-pane>
          </template>
        </el-tabs>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

<style lang="scss" scoped>
.dropdown-badge {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 48px;
  cursor: pointer;

  .header-notice-icon {
    font-size: 18px;
  }
}

.dropdown-tabs {
  .noticeList-container {
    padding: 15px 24px 0;
  }

  .notice-footer {
    display: flex;
    justify-content: center;
    padding: 8px 0 12px;
    border-top: 1px solid var(--el-border-color-lighter);
  }

  :deep(.el-tabs__header) {
    margin: 0;
  }

  :deep(.el-tabs__nav-wrap)::after {
    height: 1px;
  }

  :deep(.el-tabs__nav-wrap) {
    padding: 0 36px;
  }
}
</style>
