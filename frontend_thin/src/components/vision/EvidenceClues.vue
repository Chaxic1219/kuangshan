<script setup lang="ts">
import { ref, watch } from "vue";
import DetectionFrame from "@/components/vision/DetectionFrame.vue";

const props = defineProps<{
  record?: any;
  regions?: any[];
}>();

const mode = ref<"image" | "video">("image");

watch(
  () => props.record?.id || props.record?.frameId || "",
  () => {
    mode.value = "image";
  }
);
</script>

<template>
  <div class="clues">
    <el-radio-group v-model="mode" size="small">
      <el-radio-button value="image">图片线索</el-radio-button>
      <el-radio-button value="video">视频线索</el-radio-button>
    </el-radio-group>
    <div v-if="mode === 'image'" class="panel">
      <DetectionFrame
        v-if="record?.image"
        :record="record"
        :regions="regions || record?.regions || []"
      />
      <el-empty v-else description="该告警无图片线索" />
    </div>
    <div v-else class="panel">
      <video
        v-if="record?.video"
        class="clip"
        :src="record.video"
        controls
        playsinline
        preload="metadata"
      />
      <el-empty
        v-else
        description="该告警无视频线索（早期记录可能未生成；新告警会自动附带短视频）"
      />
    </div>
  </div>
</template>

<style scoped>
.clues {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.panel {
  min-height: 120px;
}
.clip {
  display: block;
  width: 100%;
  max-height: 420px;
  background: #111;
}
</style>
