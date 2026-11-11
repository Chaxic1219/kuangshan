<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from "vue";

const props = defineProps<{
  preview?: { kind?: string; url?: string; whep?: string } | null;
  /** 由父组件处理失败回退时，不展示 iframe/底部提示 */
  embedded?: boolean;
  /** 铺满容器（叠框场景）；默认 contain */
  fit?: "contain" | "fill";
}>();
const emit = defineEmits<{
  fail: [string];
  meta: [{ w: number; h: number }];
  loading: [];
  ready: [];
}>();
const videoEl = ref<HTMLVideoElement | null>(null);
const error = ref("");
let pc: RTCPeerConnection | null = null;
let aborted = false;
/** 已启动的预览指纹；store 每 1.5s 换新 preview 对象时避免误重连。 */
let activeKey = "";
let startGeneration = 0;

function previewKey(preview: typeof props.preview) {
  if (!preview) return "";
  return `${preview.kind || ""}|${preview.url || ""}|${preview.whep || ""}`;
}

function onMeta(event: Event) {
  const el = event.target as HTMLVideoElement;
  if (el.videoWidth > 0 && el.videoHeight > 0) {
    emit("meta", { w: el.videoWidth, h: el.videoHeight });
  }
}

function markReady(event?: Event) {
  if (event) onMeta(event);
  emit("ready");
}

function reportFail(message: string) {
  error.value = message;
  activeKey = "";
  emit("fail", message);
}

async function playWhep(whep: string, generation: number) {
  const connection = new RTCPeerConnection();
  pc = connection;
  connection.addTransceiver("video", { direction: "recvonly" });
  connection.addTransceiver("audio", { direction: "recvonly" });
  connection.ontrack = event => {
    if (aborted || generation !== startGeneration || !videoEl.value) return;
    videoEl.value.srcObject = event.streams[0];
    void videoEl.value.play().then(() => {
      if (!aborted && generation === startGeneration) emit("ready");
    }).catch(() => {});
  };
  connection.onconnectionstatechange = () => {
    if (generation !== startGeneration || pc !== connection) return;
    // disconnected 可能短暂抖动，仅 failed 才回退，避免画面来回闪。
    if (connection.connectionState === "failed") {
      reportFail("WebRTC 连接失败");
      connection.close();
      if (pc === connection) pc = null;
    }
  };
  const offer = await connection.createOffer();
  await connection.setLocalDescription(offer);
  const response = await fetch(whep, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: offer.sdp || ""
  });
  if (!response.ok) {
    throw new Error(`WebRTC 预览失败（${response.status}），请确认已启动推流服务`);
  }
  const answer = await response.text();
  if (aborted || generation !== startGeneration || pc !== connection) return;
  await connection.setRemoteDescription({ type: "answer", sdp: answer });
}

function stop() {
  aborted = true;
  startGeneration += 1;
  activeKey = "";
  if (videoEl.value) videoEl.value.srcObject = null;
  if (pc) {
    pc.close();
    pc = null;
  }
}

watch(
  // 必须返回稳定字符串：每次数组字面量都会被当成新值，导致 1.5s 轮询反复掐断 WebRTC。
  () => previewKey(props.preview),
  async key => {
    if (key && key === activeKey && pc) return;
    stop();
    aborted = false;
    const generation = startGeneration;
    error.value = "";
    emit("loading");
    if (!props.preview?.url) {
      if (props.embedded) reportFail("无可用预览地址");
      return;
    }
    if (props.preview.kind === "video") {
      activeKey = key;
      return;
    }
    if (props.preview.kind === "webrtc" && props.preview.whep) {
      try {
        activeKey = key;
        await playWhep(props.preview.whep, generation);
        if (generation !== startGeneration) return;
      } catch (e) {
        if (!aborted && generation === startGeneration) {
          reportFail((e as Error).message || "WebRTC 预览失败");
        }
      }
      return;
    }
    if (props.preview.kind === "webrtc") {
      const msg = "WebRTC 预览缺少 WHEP 地址";
      if (props.embedded) reportFail(msg);
      else error.value = msg;
    }
  },
  { immediate: true }
);

onBeforeUnmount(stop);
</script>

<template>
  <div class="live-player" :class="{ embedded, fill: fit === 'fill' }">
    <video
      v-if="preview?.kind === 'video'"
      :src="preview.url"
      :controls="!embedded"
      autoplay
      muted
      loop
      playsinline
      @loadedmetadata="onMeta"
      @loadeddata="markReady"
      @error="reportFail('原视频预览加载失败')"
    />
    <template v-else-if="preview?.kind === 'webrtc'">
      <video
        ref="videoEl"
        autoplay
        muted
        playsinline
        :controls="!embedded"
        @loadedmetadata="onMeta"
        @playing="markReady"
      />
      <iframe
        v-if="error && !embedded"
        class="webrtc-fallback"
        :src="`${preview.url}?muted=true&controls=true&autoplay=true`"
        title="WebRTC 预览"
        allow="autoplay; fullscreen"
      />
    </template>
    <p v-if="error && !embedded" class="notice">{{ error }}</p>
  </div>
</template>

<style scoped>
.live-player {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 180px;
  background: #09151f;
  border-radius: 8px;
  overflow: hidden;
}
.live-player.embedded {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  min-height: 0;
  border-radius: 0;
  background: transparent;
}
.live-player video,
.webrtc-fallback {
  display: block;
  width: 100%;
  height: 100%;
  min-height: 220px;
  max-height: 70vh;
  border: 0;
  object-fit: contain;
  background: transparent;
}
.live-player.embedded video {
  min-height: 0;
  max-height: none;
  position: absolute;
  inset: 0;
}
.live-player.fill video {
  object-fit: fill;
}
.notice {
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  margin: 0;
  padding: 8px;
  background: #19242de6;
  color: #ffd48b;
  font-size: 12px;
}
</style>
