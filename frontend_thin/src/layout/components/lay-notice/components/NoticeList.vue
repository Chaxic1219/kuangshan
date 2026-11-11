<script setup lang="ts">
import { PropType } from "vue";
import { ListItem } from "../data";
import NoticeItem from "./NoticeItem.vue";

defineProps({
  list: {
    type: Array as PropType<Array<ListItem>>,
    default: () => []
  },
  emptyText: {
    type: String,
    default: ""
  }
});
const emit = defineEmits<{ select: [item: ListItem] }>();
</script>

<template>
  <div v-if="list.length">
    <NoticeItem
      v-for="(item, index) in list"
      :key="item.id || index"
      :notice-item="item"
      @click="emit('select', item)"
    />
  </div>
  <el-empty v-else :description="emptyText" />
</template>
