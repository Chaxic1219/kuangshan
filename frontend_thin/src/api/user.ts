import { http } from "@/utils/http";

export type UserResult = {
  success: boolean;
  message?: string;
  data: {
    /** 头像 */
    avatar: string;
    /** 用户名 */
    username: string;
    /** 昵称 */
    nickname: string;
    /** 当前登录用户的角色 */
    roles: Array<string>;
    /** 按钮级别权限 */
    permissions: Array<string>;
    /** `token` */
    accessToken: string;
    /** 用于调用刷新`accessToken`的接口时所需的`token` */
    refreshToken: string;
    /** `accessToken`的过期时间（格式'xxxx/xx/xx xx:xx:xx'） */
    expires: Date;
    /** 是否必须修改密码 */
    mustChangePassword?: boolean;
  };
};

export type RefreshTokenResult = {
  success: boolean;
  message?: string;
  data: {
    /** `token` */
    accessToken: string;
    /** 用于调用刷新`accessToken`的接口时所需的`token` */
    refreshToken: string;
    /** `accessToken`的过期时间（格式'xxxx/xx/xx xx:xx:xx'） */
    expires: Date;
  };
};

/** 登录（本机真实账号） */
export const getLogin = (data?: object) => {
  return http.request<UserResult>("post", "/api/auth/login", { data });
};

/** 刷新`token` */
export const refreshTokenApi = (data?: object) => {
  return http.request<RefreshTokenResult>("post", "/api/auth/refresh", {
    data
  });
};

/** 修改密码 */
export const changePasswordApi = (data?: object) => {
  return http.request<{ success: boolean; message?: string }>(
    "post",
    "/api/auth/change-password",
    { data }
  );
};

export type ManagedUser = {
  username: string;
  nickname: string;
  roles: string[];
  permissions?: string[];
  mustChangePassword: boolean;
  avatar?: string;
};

/** 更新个人资料（昵称 / 头像） */
export const updateProfileApi = (data: {
  nickname?: string;
  avatar?: string;
}) => {
  return http.request<{
    success: boolean;
    message?: string;
    user?: ManagedUser;
  }>("post", "/api/auth/profile", { data });
};

/** 用户列表（仅管理员） */
export const listUsersApi = () => {
  return http.request<{
    success: boolean;
    message?: string;
    users: ManagedUser[];
  }>("get", "/api/auth/users");
};

/** 新建用户 */
export const createUserApi = (data: {
  username: string;
  password: string;
  nickname?: string;
  roles?: string[];
}) => {
  return http.request<{
    success: boolean;
    message?: string;
    user?: ManagedUser;
  }>("post", "/api/auth/users", { data });
};

/** 更新昵称/角色 */
export const updateUserApi = (
  username: string,
  data: { nickname?: string; roles?: string[] }
) => {
  return http.request<{
    success: boolean;
    message?: string;
    user?: ManagedUser;
  }>("post", `/api/auth/users/${encodeURIComponent(username)}/update`, {
    data
  });
};

/** 管理员重置口令 */
export const resetUserPasswordApi = (
  username: string,
  data: { newPassword: string }
) => {
  return http.request<{
    success: boolean;
    message?: string;
    mustChangePassword?: boolean;
    user?: ManagedUser;
  }>(
    "post",
    `/api/auth/users/${encodeURIComponent(username)}/reset-password`,
    { data }
  );
};

/** 删除用户 */
export const deleteUserApi = (username: string) => {
  return http.request<{ success: boolean; message?: string }>(
    "post",
    `/api/auth/users/${encodeURIComponent(username)}/delete`,
    { data: {} }
  );
};
