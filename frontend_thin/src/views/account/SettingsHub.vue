<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import type { FormInstance, FormRules, UploadRequestOptions } from "element-plus";
import { ElMessage } from "element-plus";
import {
  changePasswordApi,
  updateProfileApi
} from "@/api/user";
import {
  clearMustChangePassword,
  patchUserProfile
} from "@/utils/auth";
import { useUserStoreHook } from "@/store/modules/user";
import DefaultAvatar from "@/assets/user.jpg";
import Contacts from "./Contacts.vue";
import Users from "./Users.vue";

const route = useRoute();
const router = useRouter();
const user = useUserStoreHook();
const isAdmin = computed(() => (user.roles || []).includes("admin"));

const TAB_KEYS = ["profile", "password", "contacts", "users"] as const;
type TabKey = (typeof TAB_KEYS)[number];

function normalizeTab(raw: unknown): TabKey {
  const value = String(raw || "");
  if (value === "users" && !isAdmin.value) return "profile";
  if ((TAB_KEYS as readonly string[]).includes(value)) return value as TabKey;
  return "profile";
}

const active = ref<TabKey>(normalizeTab(route.query.tab));

watch(
  () => route.query.tab,
  value => {
    active.value = normalizeTab(value);
  }
);

watch(active, value => {
  if (String(route.query.tab || "") === value) return;
  router.replace({ path: "/settings/profile", query: { tab: value } });
});

const profileBusy = ref(false);
const profileForm = reactive({
  nickname: user.nickname || user.username || "",
  avatar: user.avatar || ""
});

const avatarPreview = computed(() => profileForm.avatar || DefaultAvatar);

async function compressAvatar(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file);
  const size = 128;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = size;
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new Error("无法处理图片");
  const scale = Math.max(size / bitmap.width, size / bitmap.height);
  const w = bitmap.width * scale;
  const h = bitmap.height * scale;
  ctx.drawImage(bitmap, (size - w) / 2, (size - h) / 2, w, h);
  bitmap.close();
  const dataUrl = canvas.toDataURL("image/jpeg", 0.72);
  if (dataUrl.length > 180_000) {
    throw new Error("头像压缩后仍过大，请换一张更小的图片");
  }
  return dataUrl;
}

async function onAvatarUpload(options: UploadRequestOptions) {
  try {
    const file = options.file as File;
    if (!file.type.startsWith("image/")) {
      ElMessage.error("请选择图片文件");
      return;
    }
    profileForm.avatar = await compressAvatar(file);
    options.onSuccess?.({});
  } catch (e: any) {
    ElMessage.error(e?.message || "头像处理失败");
    options.onError?.(e);
  }
}

function clearAvatar() {
  profileForm.avatar = "";
}

async function saveProfile() {
  const nickname = profileForm.nickname.trim();
  if (!nickname) {
    ElMessage.warning("请填写显示名称");
    return;
  }
  profileBusy.value = true;
  try {
    const result = await updateProfileApi({
      nickname,
      avatar: profileForm.avatar
    });
    if (!result?.success) {
      ElMessage.error(result?.message || "保存失败");
      return;
    }
    const nextNick = result.user?.nickname || nickname;
    const nextAvatar = result.user?.avatar ?? profileForm.avatar;
    profileForm.nickname = nextNick;
    profileForm.avatar = nextAvatar;
    patchUserProfile({ nickname: nextNick, avatar: nextAvatar });
    ElMessage.success("个人资料已保存");
  } catch (e: any) {
    ElMessage.error(
      e?.response?.data?.message || e?.message || "保存失败，请稍后重试"
    );
  } finally {
    profileBusy.value = false;
  }
}

const pwdBusy = ref(false);
const pwdFormRef = ref<FormInstance>();
const pwdForm = reactive({
  oldPassword: "",
  newPassword: "",
  confirmPassword: ""
});
const pwdRules: FormRules = {
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
        if (value !== pwdForm.newPassword)
          return callback(new Error("两次输入的新密码不一致"));
        callback();
      },
      trigger: "blur"
    }
  ]
};

async function submitPassword() {
  if (!pwdFormRef.value) return;
  await pwdFormRef.value.validate(async valid => {
    if (!valid) return;
    pwdBusy.value = true;
    try {
      const result = await changePasswordApi({
        username: user.username || "admin",
        oldPassword: pwdForm.oldPassword,
        newPassword: pwdForm.newPassword
      });
      if (!result?.success) {
        ElMessage.error(result?.message || "修改失败");
        return;
      }
      clearMustChangePassword();
      ElMessage.success("密码已修改，请使用新密码重新登录");
      user.logOut();
    } catch (e: any) {
      ElMessage.error(
        e?.response?.data?.message ||
          e?.message ||
          "修改失败，请确认旧密码后重试"
      );
    } finally {
      pwdBusy.value = false;
    }
  });
}
</script>

<template>
  <section class="settings-hub">
    <header class="hub-head">
      <div>
        <h1>系统设置</h1>
        <p>
          管理个人资料与登录密码
          <template v-if="isAdmin">；管理员还可维护联系人与账号</template>
          <template v-else>；紧急联系人可供值班处置使用</template>
        </p>
      </div>
    </header>

    <el-tabs v-model="active" class="hub-tabs">
      <el-tab-pane label="个人资料" name="profile">
        <div class="pane-body">
          <div class="avatar-row">
            <img :src="avatarPreview" class="avatar-preview" alt="头像预览" />
            <div class="avatar-actions">
              <el-upload
                :show-file-list="false"
                accept="image/*"
                :http-request="onAvatarUpload"
              >
                <el-button>更换头像</el-button>
              </el-upload>
              <el-button text @click="clearAvatar">恢复默认</el-button>
              <p class="hint">将自动压缩为 128×128，便于本机存储。</p>
            </div>
          </div>
          <el-form label-width="100px" style="max-width: 480px" @submit.prevent>
            <el-form-item label="登录账号">
              <el-input :model-value="user.username" disabled />
            </el-form-item>
            <el-form-item label="显示名称">
              <el-input
                v-model="profileForm.nickname"
                maxlength="64"
                placeholder="昵称"
              />
            </el-form-item>
            <el-form-item label="角色">
              <el-tag
                v-for="role in user.roles || []"
                :key="role"
                class="role-tag"
                size="small"
              >
                {{ role === "admin" ? "管理员" : "值班员" }}
              </el-tag>
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="profileBusy" @click="saveProfile"
                >保存资料</el-button
              >
            </el-form-item>
          </el-form>
        </div>
      </el-tab-pane>

      <el-tab-pane label="修改密码" name="password">
        <div class="pane-body">
          <p class="pane-desc">
            密码不少于 8 位且同时包含字母与数字。修改成功后需重新登录。
          </p>
          <el-form
            ref="pwdFormRef"
            :model="pwdForm"
            :rules="pwdRules"
            label-width="100px"
            style="max-width: 480px"
            @submit.prevent
          >
            <el-form-item label="旧密码" prop="oldPassword">
              <el-input
                v-model="pwdForm.oldPassword"
                type="password"
                show-password
                autocomplete="current-password"
              />
            </el-form-item>
            <el-form-item label="新密码" prop="newPassword">
              <el-input
                v-model="pwdForm.newPassword"
                type="password"
                show-password
                autocomplete="new-password"
              />
            </el-form-item>
            <el-form-item label="确认新密码" prop="confirmPassword">
              <el-input
                v-model="pwdForm.confirmPassword"
                type="password"
                show-password
                autocomplete="new-password"
              />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="pwdBusy" @click="submitPassword"
                >确定</el-button
              >
            </el-form-item>
          </el-form>
        </div>
      </el-tab-pane>

      <el-tab-pane label="紧急联系人" name="contacts">
        <div class="embed-pane">
          <Contacts />
        </div>
      </el-tab-pane>

      <el-tab-pane v-if="isAdmin" label="用户管理" name="users">
        <div class="embed-pane">
          <Users />
        </div>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<style scoped>
.settings-hub {
  padding: 24px;
  color: var(--vision-text);
}
.hub-head {
  margin-bottom: 12px;
}
.hub-head h1 {
  margin: 0 0 8px;
}
.hub-head p {
  margin: 0;
  color: var(--vision-text-secondary);
  font-size: 13px;
}
.pane-body {
  padding-top: 8px;
}
.pane-desc {
  margin: 0 0 16px;
  color: var(--vision-text-secondary);
  font-size: 13px;
}
.avatar-row {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-bottom: 20px;
}
.avatar-preview {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  object-fit: cover;
  background: #f0f2f5;
}
.avatar-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.hint {
  width: 100%;
  margin: 0;
  font-size: 12px;
  color: var(--vision-text-secondary);
}
.role-tag {
  margin-right: 6px;
}
.embed-pane :deep(.contacts-page),
.embed-pane :deep(.users-page) {
  padding: 8px 0 0;
}
.embed-pane :deep(.contacts-head h1),
.embed-pane :deep(.users-head h1) {
  font-size: 16px;
}
</style>
