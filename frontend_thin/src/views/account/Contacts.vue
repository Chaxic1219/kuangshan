<script setup lang="ts">
import { onMounted, ref } from "vue";
import { ElMessage } from "element-plus";
import { request } from "@/api/vision";

const loading = ref(false);
const saving = ref(false);
const contacts = ref<any[]>([]);

async function loadContacts() {
  loading.value = true;
  try {
    const result = await request("/api/contacts");
    contacts.value = (result.contacts || []).map((c: any) => ({ ...c }));
  } catch (e: any) {
    ElMessage.error(e.message || "加载紧急联系人失败");
  } finally {
    loading.value = false;
  }
}

function addContact() {
  contacts.value.push({
    id: "",
    name: "",
    org: "",
    phone: "",
    note: ""
  });
}

function removeContact(index: number) {
  contacts.value.splice(index, 1);
}

async function saveContacts() {
  saving.value = true;
  try {
    const result = await request("/api/contacts", {
      contacts: contacts.value
    });
    contacts.value = result.contacts || [];
    ElMessage.success("紧急联系人已保存");
  } catch (e: any) {
    ElMessage.error(e.message || "保存失败");
  } finally {
    saving.value = false;
  }
}

onMounted(() => {
  void loadContacts();
});
</script>

<template>
  <section class="contacts-page" v-loading="loading">
    <header class="contacts-head">
      <div>
        <h1>紧急联系人</h1>
        <p>供告警处置与证据卡展示姓名与手机号；值班员与管理员均可维护。</p>
      </div>
      <el-button @click="addContact">新增</el-button>
    </header>

    <div v-if="contacts.length" class="contact-list">
      <div
        v-for="(row, index) in contacts"
        :key="row.id || index"
        class="contact-row"
      >
        <el-input v-model="row.name" placeholder="姓名 *" maxlength="64" />
        <el-input v-model="row.org" placeholder="机构" maxlength="64" />
        <el-input v-model="row.phone" placeholder="手机号" maxlength="32" />
        <el-input v-model="row.note" placeholder="备注" maxlength="120" />
        <el-button text type="danger" @click="removeContact(index)"
          >删除</el-button
        >
      </div>
    </div>
    <el-empty v-else description="暂无联系人，点击新增" :image-size="72" />

    <div class="contact-actions">
      <el-button type="primary" :loading="saving" @click="saveContacts"
        >保存联系人</el-button
      >
    </div>
  </section>
</template>

<style scoped>
.contacts-page {
  padding: 20px 24px 32px;
}
.contacts-head,
.contact-actions {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}
.contacts-head {
  margin-bottom: 16px;
}
.contacts-head h1 {
  margin: 0 0 6px;
  font-size: 1.35rem;
}
.contacts-head p {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 0.9rem;
}
.contact-list {
  display: grid;
  gap: 10px;
  margin: 0 0 16px;
}
.contact-row {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr 1.2fr auto;
  gap: 8px;
  align-items: center;
}
.contact-actions {
  justify-content: flex-start;
}
@media (max-width: 1100px) {
  .contact-row {
    grid-template-columns: 1fr;
  }
}
</style>
