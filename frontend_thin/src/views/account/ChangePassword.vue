<script setup lang="ts">
import { reactive, ref } from "vue";
import type { FormInstance, FormRules } from "element-plus";
import { ElMessage } from "element-plus";
import { changePasswordApi } from "@/api/user";
import { clearMustChangePassword } from "@/utils/auth";
import { useUserStoreHook } from "@/store/modules/user";

const busy = ref(false);
const formRef = ref<FormInstance>();
const forced = !!useUserStoreHook().mustChangePassword;
const form = reactive({
  oldPassword: "",
  newPassword: "",
  confirmPassword: ""
});

const rules: FormRules = {
  oldPassword: [{ required: true, message: "请输入旧密码", trigger: "blur" }],
  newPassword: [
    {
      validator: (_r, value, callback) => {
        if (!value || value.length < 8)
          return callback(new Error("密码不少于 8 位"));
        if (!/[A-Za-z]/.test(value) || !/\d/.test(value))
          return callback(new Error("密码必须同时包含字母和数字"));
        callback();
      },
      trigger: "blur"
    }
  ],
  confirmPassword: [
    {
      validator: (_r, value, callback) => {
        if (value !== form.newPassword)
          return callback(new Error("两次输入的新密码不一致"));
        callback();
      },
      trigger: "blur"
    }
  ]
};

async function submit() {
  if (!formRef.value) return;
  await formRef.value.validate(async valid => {
    if (!valid) return;
    busy.value = true;
    try {
      const result = await changePasswordApi({
        username: useUserStoreHook().username || "admin",
        oldPassword: form.oldPassword,
        newPassword: form.newPassword
      });
      if (!result?.success) {
        ElMessage.error(result?.message || "修改失败");
        return;
      }
      clearMustChangePassword();
      ElMessage.success("密码已修改，请使用新密码重新登录");
      useUserStoreHook().logOut();
    } catch (e: any) {
      const msg =
        e?.response?.data?.message ||
        e?.message ||
        "修改失败，请确认旧密码后重试";
      ElMessage.error(msg);
    } finally {
      busy.value = false;
    }
  });
}
</script>

<template>
  <section class="pwd-page">
    <header>
      <h1>修改密码</h1>
      <p v-if="forced">首次登录须修改密码后才能进入系统（不少于 8 位，含字母和数字）。</p>
      <p v-else>建议定期修改密码。修改成功后若为强制改密流程将退回登录页。</p>
    </header>
    <el-form
      ref="formRef"
      :model="form"
      :rules="rules"
      label-width="100px"
      style="max-width: 480px"
      @submit.prevent
    >
      <el-form-item label="旧密码" prop="oldPassword">
        <el-input
          v-model="form.oldPassword"
          type="password"
          show-password
          autocomplete="current-password"
        />
      </el-form-item>
      <el-form-item label="新密码" prop="newPassword">
        <el-input
          v-model="form.newPassword"
          type="password"
          show-password
          autocomplete="new-password"
        />
      </el-form-item>
      <el-form-item label="确认新密码" prop="confirmPassword">
        <el-input
          v-model="form.confirmPassword"
          type="password"
          show-password
          autocomplete="new-password"
        />
      </el-form-item>
      <el-form-item>
        <el-button type="primary" :loading="busy" @click="submit"
          >确定</el-button
        >
      </el-form-item>
    </el-form>
  </section>
</template>

<style scoped>
.pwd-page {
  padding: 24px;
  color: var(--vision-text);
}
header {
  margin-bottom: 20px;
}
h1 {
  margin: 0 0 8px;
}
p {
  margin: 0;
  color: var(--vision-text-secondary);
  font-size: 13px;
}
</style>
