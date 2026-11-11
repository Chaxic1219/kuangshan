<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessage, ElMessageBox } from "element-plus";
import type { FormInstance, FormRules } from "element-plus";
import {
  createUserApi,
  deleteUserApi,
  listUsersApi,
  resetUserPasswordApi,
  updateUserApi,
  type ManagedUser
} from "@/api/user";
import { useUserStoreHook } from "@/store/modules/user";

const loading = ref(false);
const rows = ref<ManagedUser[]>([]);
const dialog = ref(false);
const resetDialog = ref(false);
const editDialog = ref(false);
const busy = ref(false);
const targetUser = ref("");

const createFormRef = ref<FormInstance>();
const resetFormRef = ref<FormInstance>();
const editFormRef = ref<FormInstance>();

const createForm = reactive({
  username: "",
  nickname: "",
  password: "",
  roles: ["operator"] as string[]
});

const resetForm = reactive({
  newPassword: ""
});

const editForm = reactive({
  nickname: "",
  roles: ["operator"] as string[]
});

const createRules: FormRules = {
  username: [
    {
      required: true,
      pattern: /^[A-Za-z][A-Za-z0-9_]{2,31}$/,
      message: "字母开头，3～32 位字母数字或下划线",
      trigger: "blur"
    }
  ],
  password: [
    {
      validator: (_r, value, callback) => {
        if (!value || value.length < 8)
          return callback(new Error("密码不少于 8 位"));
        if (!/[A-Za-z]/.test(value) || !/\d/.test(value))
          return callback(new Error("须同时包含字母和数字"));
        callback();
      },
      trigger: "blur"
    }
  ]
};

const resetRules: FormRules = {
  newPassword: createRules.password
};

async function load() {
  loading.value = true;
  try {
    const result = await listUsersApi();
    if (!result?.success && result?.message) {
      ElMessage.error(result.message);
      rows.value = [];
      return;
    }
    rows.value = result?.users || [];
  } catch (e: any) {
    const status = e?.response?.status;
    const msg =
      e?.response?.data?.message || e?.message || "加载用户失败";
    ElMessage.error(msg);
    // 服务重启或会话丢失后，前端仍显示已登录，这里强制回登录页
    if (status === 401 || status === 403) {
      useUserStoreHook().logOut();
    }
  } finally {
    loading.value = false;
  }
}

function openCreate() {
  createForm.username = "";
  createForm.nickname = "";
  createForm.password = "";
  createForm.roles = ["operator"];
  dialog.value = true;
}

async function submitCreate() {
  if (!createFormRef.value) return;
  await createFormRef.value.validate(async valid => {
    if (!valid) return;
    busy.value = true;
    try {
      const result = await createUserApi({
        username: createForm.username.trim(),
        nickname: createForm.nickname.trim(),
        password: createForm.password,
        roles: createForm.roles
      });
      if (!result?.success) {
        ElMessage.error(result?.message || "创建失败");
        return;
      }
      ElMessage.success("已创建，该用户首次登录须修改密码");
      dialog.value = false;
      await load();
    } catch (e: any) {
      ElMessage.error(
        e?.response?.data?.message || e?.message || "创建失败"
      );
    } finally {
      busy.value = false;
    }
  });
}

function openEdit(row: ManagedUser) {
  targetUser.value = row.username;
  editForm.nickname = row.nickname || "";
  editForm.roles = [...(row.roles || ["operator"])];
  editDialog.value = true;
}

async function submitEdit() {
  if (!editFormRef.value) return;
  await editFormRef.value.validate(async valid => {
    if (!valid) return;
    busy.value = true;
    try {
      const result = await updateUserApi(targetUser.value, {
        nickname: editForm.nickname.trim(),
        roles: editForm.roles
      });
      if (!result?.success) {
        ElMessage.error(result?.message || "保存失败");
        return;
      }
      ElMessage.success("已保存");
      editDialog.value = false;
      await load();
    } catch (e: any) {
      ElMessage.error(
        e?.response?.data?.message || e?.message || "保存失败"
      );
    } finally {
      busy.value = false;
    }
  });
}

function openReset(row: ManagedUser) {
  targetUser.value = row.username;
  resetForm.newPassword = "";
  resetDialog.value = true;
}

async function submitReset() {
  if (!resetFormRef.value) return;
  await resetFormRef.value.validate(async valid => {
    if (!valid) return;
    busy.value = true;
    try {
      const result = await resetUserPasswordApi(targetUser.value, {
        newPassword: resetForm.newPassword
      });
      if (!result?.success) {
        ElMessage.error(result?.message || "重置失败");
        return;
      }
      const tip =
        targetUser.value === "admin"
          ? "admin 口令已重置"
          : "口令已重置，该用户下次登录须修改密码";
      ElMessage.success(tip);
      resetDialog.value = false;
      await load();
    } catch (e: any) {
      ElMessage.error(
        e?.response?.data?.message || e?.message || "重置失败"
      );
    } finally {
      busy.value = false;
    }
  });
}

async function removeUser(row: ManagedUser) {
  if (row.username === "admin") {
    ElMessage.warning("不能删除 admin");
    return;
  }
  try {
    await ElMessageBox.confirm(
      `确认删除用户「${row.username}」？`,
      "删除用户",
      { type: "warning", confirmButtonText: "删除", cancelButtonText: "取消" }
    );
  } catch {
    return;
  }
  busy.value = true;
  try {
    const result = await deleteUserApi(row.username);
    if (!result?.success) {
      ElMessage.error(result?.message || "删除失败");
      return;
    }
    ElMessage.success("已删除");
    await load();
  } catch (e: any) {
    ElMessage.error(
      e?.response?.data?.message || e?.message || "删除失败"
    );
  } finally {
    busy.value = false;
  }
}

function roleLabel(roles: string[] = []) {
  if (roles.includes("admin")) return "管理员";
  return "值班员";
}

onMounted(() => {
  void load();
});
</script>

<template>
  <section class="users-page">
    <header class="users-head">
      <div>
        <h1>用户管理</h1>
        <p>
          仅管理员可操作。新建与重置口令后，非 admin
          用户首次登录必须修改密码。
        </p>
      </div>
      <el-button type="primary" :disabled="busy" @click="openCreate"
        >新建用户</el-button
      >
    </header>

    <el-table v-loading="loading" :data="rows" stripe border>
      <el-table-column prop="username" label="用户名" min-width="120" />
      <el-table-column prop="nickname" label="昵称" min-width="120" />
      <el-table-column label="角色" min-width="100">
        <template #default="{ row }">{{ roleLabel(row.roles) }}</template>
      </el-table-column>
      <el-table-column label="须改密" width="90" align="center">
        <template #default="{ row }">
          <span :class="row.mustChangePassword ? 'flag-yes' : 'flag-no'">
            {{ row.mustChangePassword ? "是" : "否" }}
          </span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
          <el-button link type="warning" @click="openReset(row)"
            >重置口令</el-button
          >
          <el-button
            link
            type="danger"
            :disabled="row.username === 'admin'"
            @click="removeUser(row)"
            >删除</el-button
          >
        </template>
      </el-table-column>
    </el-table>

    <el-dialog v-model="dialog" title="新建用户" width="420px" destroy-on-close>
      <el-form
        ref="createFormRef"
        :model="createForm"
        :rules="createRules"
        label-width="88px"
      >
        <el-form-item label="用户名" prop="username">
          <el-input v-model="createForm.username" maxlength="32" />
        </el-form-item>
        <el-form-item label="昵称">
          <el-input v-model="createForm.nickname" maxlength="64" />
        </el-form-item>
        <el-form-item label="临时口令" prop="password">
          <el-input
            v-model="createForm.password"
            type="password"
            show-password
            maxlength="64"
          />
        </el-form-item>
        <el-form-item label="角色">
          <el-select v-model="createForm.roles" multiple>
            <el-option label="管理员" value="admin" />
            <el-option label="值班员" value="operator" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitCreate"
          >创建</el-button
        >
      </template>
    </el-dialog>

    <el-dialog
      v-model="editDialog"
      :title="`编辑 ${targetUser}`"
      width="420px"
      destroy-on-close
    >
      <el-form ref="editFormRef" :model="editForm" label-width="88px">
        <el-form-item label="昵称">
          <el-input v-model="editForm.nickname" maxlength="64" />
        </el-form-item>
        <el-form-item label="角色">
          <el-select
            v-model="editForm.roles"
            multiple
            :disabled="targetUser === 'admin'"
          >
            <el-option label="管理员" value="admin" />
            <el-option label="值班员" value="operator" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editDialog = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitEdit"
          >保存</el-button
        >
      </template>
    </el-dialog>

    <el-dialog
      v-model="resetDialog"
      :title="`重置口令 · ${targetUser}`"
      width="420px"
      destroy-on-close
    >
      <el-form
        ref="resetFormRef"
        :model="resetForm"
        :rules="resetRules"
        label-width="88px"
      >
        <el-form-item label="新临时口令" prop="newPassword">
          <el-input
            v-model="resetForm.newPassword"
            type="password"
            show-password
            maxlength="64"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="resetDialog = false">取消</el-button>
        <el-button type="primary" :loading="busy" @click="submitReset"
          >重置</el-button
        >
      </template>
    </el-dialog>
  </section>
</template>

<style scoped>
.users-page {
  padding: 20px 24px 32px;
}
.users-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.users-head h1 {
  margin: 0 0 6px;
  font-size: 1.35rem;
}
.users-head p {
  margin: 0;
  color: var(--el-text-color-secondary);
  font-size: 0.9rem;
}
.flag-yes {
  color: var(--el-color-warning);
  font-weight: 600;
}
.flag-no {
  color: var(--el-text-color-secondary);
}
</style>
