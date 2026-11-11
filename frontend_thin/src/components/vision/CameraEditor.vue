<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, reactive, ref } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import { ElMessageBox } from "element-plus";
import { request } from "@/api/vision";
import RegionEditor from "./RegionEditor.vue";

const props = withDefaults(
  defineProps<{ camera: any | null; state: any; connected?: boolean }>(),
  { connected: true }
);
const emit = defineEmits(["saved", "close"]);
// 草稿只在打开时复制，状态轮询不得覆盖用户正在编辑的内容。
const draft = reactive<any>(
  JSON.parse(
    JSON.stringify(
      props.camera || {
        id: "",
        name: "",
        location: "",
        url: "",
        cover: "",
        tasks: [],
        regions: []
      }
    )
  )
);
const initial = JSON.stringify(draft);
const step = ref(0),
  busy = ref(false),
  error = ref(""),
  deviceError = ref(""),
  source = ref("address");
const devices = ref<any[]>([]),
  samples = ref<any[]>([]),
  deviceId = ref(""),
  drawing = ref(false),
  devicesLoading = ref(false);
const controller = new AbortController();
const events = computed<any>(() => props.state?.events || {});
const dirty = computed(
  () => JSON.stringify(draft) !== initial || drawing.value
);
let saved = false;
const ready = computed(() => props.connected && !!props.state?.models?.length);
function changeUrl(value: string) {
  if (value !== draft.url) {
    draft.url = value;
    draft.cover = "";
    draft.coverToken = "";
  }
}
function sample(value: string) {
  changeUrl(value);
  if (!draft.name)
    draft.name = samples.value.find(s => s.url === value)?.name || "";
}
async function loadDevices() {
  devicesLoading.value = true;
  deviceError.value = "";
  try {
    const result = await request(
      "/api/cameras/devices",
      undefined,
      controller.signal
    );
    devices.value = result.devices || [];
    const usable = devices.value.filter(
      device => device.usable && device.connected !== false
    );
    if (!usable.some(device => device.id === deviceId.value))
      deviceId.value = usable.length === 1 ? usable[0].id : "";
    deviceError.value = result.error || "";
  } catch (e) {
    if (!controller.signal.aborted) deviceError.value = (e as Error).message;
  } finally {
    devicesLoading.value = false;
  }
}
onMounted(() => {
  loadDevices();
  request("/api/samples", undefined, controller.signal)
    .then(data => (samples.value = data))
    .catch(e => {
      if (!controller.signal.aborted)
        error.value = `演示素材加载失败：${e.message}`;
    });
  window.addEventListener("beforeunload", beforeUnload);
});
function beforeUnload(e: BeforeUnloadEvent) {
  if (dirty.value && !saved) {
    e.preventDefault();
    e.returnValue = "";
  }
}
onBeforeUnmount(() => {
  controller.abort();
  window.removeEventListener("beforeunload", beforeUnload);
});
async function allowClose() {
  if (busy.value) return false;
  if (!dirty.value || saved) return true;
  try {
    await ElMessageBox.confirm(
      "当前配置尚未保存，确定放弃修改？",
      "离开编辑器",
      {
        confirmButtonText: "放弃修改",
        cancelButtonText: "继续编辑",
        type: "warning"
      }
    );
    return true;
  } catch {
    return false;
  }
}
async function close() {
  if (await allowClose()) emit("close");
}
onBeforeRouteLeave(async () => await allowClose());
async function probe(useDevice = false) {
  if (!ready.value || busy.value) return;
  if (useDevice ? !deviceId.value : !draft.url.trim()) {
    error.value = useDevice ? "请选择可用摄像头" : "请填写视频地址";
    return;
  }
  busy.value = true;
  error.value = "";
  try {
    const result = await request(
      useDevice ? "/api/cameras/live/prepare" : "/api/cameras/probe",
      useDevice ? { deviceId: deviceId.value } : { url: draft.url.trim() },
      controller.signal
    );
    changeUrl(result.url || draft.url.trim());
    draft.cover = result.cover;
    draft.coverToken = result.coverToken;
  } catch (e) {
    if (!controller.signal.aborted) error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
function eventModelIds(event: any): string[] {
  if (Array.isArray(event?.modelChoices) && event.modelChoices.length) {
    return event.modelChoices;
  }
  return event?.model ? [event.model] : [];
}

function eventModelReady(event: any) {
  return eventModelIds(event).some(
    id => props.state?.models?.find((m: any) => m.id === id)?.ready
  );
}

function modelOptions(eventId: string) {
  const event = events.value[eventId];
  return eventModelIds(event)
    .map(id => props.state?.models?.find((m: any) => m.id === id))
    .filter(Boolean);
}

function toggleTask(id: string, enabled: boolean) {
  if (enabled) {
    const event = events.value[id];
    draft.tasks.push({
      eventId: id,
      model: event?.model || "",
      confidence: 0.4,
      interval: 0,
      cooldown: 30,
      enabled: true,
      regionIds: [],
      dark: 0.12,
      bright: 0.88
    });
  } else draft.tasks = draft.tasks.filter(t => t.eventId !== id);
}
function updateRegions(regions: any[]) {
  draft.regions = regions;
  for (const task of draft.tasks)
    task.regionIds = (task.regionIds || []).filter(id =>
      regions.some(z => z.id === id)
    );
}
function validate() {
  if (!draft.name.trim() || draft.name.length > 100)
    return "请填写 1～100 字的视频名称";
  if (!draft.url.trim() || draft.url.length > 2048) return "请填写有效视频地址";
  if (!draft.cover) return "请先测试连接并生成封面";
  if (step.value === 3) {
    if (drawing.value) return "请先闭合当前区域或取消绘制";
    if (draft.regions.some(z => !z.name.trim() || z.points.length < 3))
      return "每个区域需要名称和至少三个顶点";
    for (const task of draft.tasks) {
      if (task.dark >= task.bright) return "过暗阈值必须小于过亮阈值";
      if (
        task.enabled &&
        events.value[task.eventId]?.region &&
        !draft.regions.some(
          z => z.enabled !== false && task.regionIds.includes(z.id)
        )
      )
        return `${events.value[task.eventId].name}至少需要绑定一个启用区域`;
    }
  }
  return "";
}
async function next() {
  error.value = validate();
  if (error.value || busy.value) return;
  if (step.value < 3) {
    step.value++;
    return;
  }
  if (!ready.value) {
    error.value = "服务状态尚未初始化，请等待连接恢复";
    return;
  }
  busy.value = true;
  try {
    // 设备身份由后端依据旧记录与封面令牌确认，表单不提交身份字段。
    const result = await request(
      "/api/cameras/save",
      {
        id: draft.id,
        name: draft.name.trim(),
        location: draft.location,
        url: draft.url,
        coverToken: draft.coverToken || "",
        tasks: draft.tasks,
        regions: draft.regions
      },
      controller.signal
    );
    saved = true;
    emit("saved", result);
    emit("close");
  } catch (e) {
    if (!controller.signal.aborted) error.value = (e as Error).message;
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <el-dialog
    :model-value="true"
    :title="camera ? '配置视频' : '添加视频'"
    width="min(980px, 94vw)"
    top="5vh"
    :close-on-click-modal="false"
    :before-close="close"
  >
    <el-steps :active="step" finish-status="success" simple>
      <el-step title="视频接入" /><el-step title="场景选择" /><el-step
        title="参数配置"
      /><el-step title="区域布控" />
    </el-steps>
    <el-alert
      v-if="!ready"
      title="服务尚未连接，暂不能探测或保存"
      type="warning"
      :closable="false"
    />
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      :closable="false"
      show-icon
    />
    <el-form label-position="top" :disabled="busy" class="editor-form">
      <div v-if="step === 0">
        <div class="columns">
          <el-form-item label="视频名称（必填）"
            ><el-input
              v-model="draft.name"
              maxlength="100"
              placeholder="例如：一号井口入口" /></el-form-item
          ><el-form-item label="安装位置"
            ><el-input v-model="draft.location" maxlength="200"
          /></el-form-item>
        </div>
        <el-radio-group v-model="source"
          ><el-radio-button value="address">视频地址</el-radio-button
          ><el-radio-button value="device">本机设备</el-radio-button
          ><el-radio-button value="sample"
            >演示视频</el-radio-button
          ></el-radio-group
        >
        <div v-if="source === 'device'" class="source-panel">
          <el-select
            v-model="deviceId"
            placeholder="选择本机摄像头"
            :loading="devicesLoading"
            ><el-option
              v-for="device in devices"
              :key="device.id"
              :value="device.id"
              :label="device.name"
              :disabled="!device.usable || device.connected === false"
          /></el-select>
          <el-button :loading="devicesLoading" @click="loadDevices"
            >重新检测</el-button
          >
          <el-button
            :disabled="!ready || !deviceId"
            :loading="busy"
            @click="probe(true)"
            >使用此摄像头并测试连接</el-button
          >
          <p v-if="deviceError" class="error">{{ deviceError }}</p>
          <p v-else-if="!devicesLoading && !devices.length">未发现本机摄像头</p>
          <p class="hint">
            点击测试后准备本机 RTSP 视频源；后续通过普通视频配置保存。
          </p>
        </div>
        <el-form-item v-if="source === 'sample'" label="本机演示素材"
          ><el-select placeholder="选择演示视频" @change="sample"
            ><el-option
              v-for="item in samples"
              :key="item.url"
              :value="item.url"
              :label="item.name" /></el-select
        ></el-form-item>
        <el-form-item label="视频地址（必填）"
          ><el-input
            :model-value="draft.url"
            maxlength="2048"
            placeholder="RTSP、RTMP、HTTP(S) 视频流或服务器本地视频路径"
            @update:model-value="changeUrl"
        /></el-form-item>
        <el-button
          type="primary"
          :disabled="!ready"
          :loading="busy"
          @click="probe(false)"
          >测试连接并截取封面</el-button
        >
        <p class="hint">
          {{
            draft.cover
              ? "已有连接封面，可以继续配置；修改地址后需要重新测试。"
              : "连接成功后自动截取一帧，普通网页链接不受支持。"
          }}
        </p>
        <img
          v-if="draft.cover"
          class="cover"
          :src="draft.cover"
          alt="视频连接封面"
        />
      </div>
      <div v-if="step === 1" class="scenes">
        <div v-for="(event, id) in events" :key="id" class="scene">
          <el-checkbox
            :model-value="draft.tasks.some(t => t.eventId === id)"
            :disabled="
              !draft.tasks.some(t => t.eventId === id) && !eventModelReady(event)
            "
            @update:model-value="toggleTask(String(id), Boolean($event))"
            >{{ event.name }}</el-checkbox
          >
          <p class="hint">
            {{
              event.modelChoices?.length
                ? `可选 ${event.modelChoices.length} 种人员权重`
                : state.models?.find(m => m.id === event.model)?.name
            }}
            ·
            {{
              event.region
                ? "需要绘制区域"
                : event.alarm
                  ? "触发时记录报警"
                  : "仅展示检测结果"
            }}
          </p>
          <p v-if="!eventModelReady(event)" class="hint">模型权重尚未验证</p>
        </div>
      </div>
      <div v-if="step === 2">
        <el-empty
          v-if="!draft.tasks.length"
          description="未绑定场景，可以先保存视频"
        />
        <section v-for="task in draft.tasks" :key="task.eventId" class="task">
          <h3>{{ events[task.eventId]?.name }}</h3>
          <div class="columns">
            <el-form-item
              v-if="events[task.eventId]?.modelChoices?.length"
              label="检测模型"
            >
              <el-select
                :model-value="task.model || events[task.eventId]?.model"
                style="width: 100%"
                placeholder="选择人员检测权重"
                @update:model-value="task.model = $event"
              >
                <el-option
                  v-for="model in modelOptions(task.eventId)"
                  :key="model.id"
                  :value="model.id"
                  :label="model.name"
                  :disabled="!model.ready"
                >
                  <span>{{ model.name }}</span>
                  <span class="hint" style="margin-left: 8px">{{
                    model.architecture
                  }}</span>
                </el-option>
              </el-select>
              <p class="hint" style="margin-top: 6px">
                {{
                  state.models?.find(m => m.id === task.model)?.description ||
                  "轻量开源模型更流畅；工厂专项更重但更贴现场。"
                }}
              </p>
            </el-form-item>
            <el-form-item label="最低置信度"
              ><el-input-number
                v-model="task.confidence"
                :min="0.05"
                :max="0.99"
                :step="0.05"
            /></el-form-item>
            <el-form-item label="分析间隔（秒，0 为持续逐帧）"
              ><el-input-number
                v-model="task.interval"
                :min="0"
                :max="60"
                :step="0.1"
            /></el-form-item>
            <el-form-item label="报警间隔（秒）"
              ><el-input-number v-model="task.cooldown" :min="0" :max="3600"
            /></el-form-item>
            <el-form-item label="场景状态"
              ><el-switch v-model="task.enabled" active-text="启用"
            /></el-form-item>
            <template v-if="task.eventId === 'brightness'"
              ><el-form-item label="过暗阈值"
                ><el-input-number
                  v-model="task.dark"
                  :min="0"
                  :max="1"
                  :step="0.01" /></el-form-item
              ><el-form-item label="过亮阈值"
                ><el-input-number
                  v-model="task.bright"
                  :min="0"
                  :max="1"
                  :step="0.01" /></el-form-item
            ></template>
          </div>
        </section>
      </div>
      <div v-if="step === 3">
        <RegionEditor
          :model-value="draft.regions"
          :image="draft.cover"
          @update:model-value="updateRegions"
          @drawing="drawing = $event"
        />
        <section v-for="task in draft.tasks" :key="task.eventId" class="task">
          <h4>{{ events[task.eventId]?.name }} · 应用区域</h4>
          <el-checkbox-group v-model="task.regionIds"
            ><el-checkbox
              v-for="zone in draft.regions"
              :key="zone.id"
              :value="zone.id"
              >{{ zone.name
              }}{{ zone.enabled === false ? "（已停用）" : "" }}</el-checkbox
            ></el-checkbox-group
          >
          <p class="hint">
            {{
              events[task.eventId]?.region
                ? "至少绑定一个已启用区域。"
                : "不选择区域时分析全画面。"
            }}
          </p>
        </section>
      </div>
    </el-form>
    <template #footer
      ><el-button :disabled="busy" @click="close">取消</el-button
      ><el-button
        v-if="step > 0"
        :disabled="busy || drawing"
        @click="
          step--;
          error = '';
        "
        >上一步</el-button
      ><el-button
        type="primary"
        :disabled="!ready"
        :loading="busy"
        @click="next"
        >{{ step === 3 ? "保存配置" : "下一步" }}</el-button
      ></template
    >
  </el-dialog>
</template>

<style scoped>
.editor-form {
  margin-top: 24px;
}
.columns {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 24px;
}
.source-panel {
  margin: 18px 0;
}
.source-panel .el-select {
  width: 260px;
  margin-right: 12px;
}
.cover {
  display: block;
  max-width: 100%;
  max-height: 280px;
  border-radius: 8px;
}
.hint {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.7;
}
.error {
  color: var(--el-color-danger);
}
.scenes {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.scene,
.task {
  border: 1px solid var(--el-border-color);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 12px;
}
.el-alert {
  margin-top: 14px;
}
@media (max-width: 650px) {
  .columns,
  .scenes {
    grid-template-columns: 1fr;
  }
  .source-panel .el-select {
    width: 100%;
    margin-bottom: 12px;
  }
}
</style>
