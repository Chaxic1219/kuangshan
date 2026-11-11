const Layout = () => import("@/layout/index.vue");

/** 视联洞察业务页：登录后进入侧栏与标签页。 */
export default [
  {
    path: "/account",
    name: "VisionAccount",
    component: Layout,
    redirect: "/account/password",
    meta: {
      title: "账号",
      showLink: false,
      rank: 99
    },
    children: [
      {
        path: "/account/password",
        name: "ChangePassword",
        component: () => import("@/views/account/ChangePassword.vue"),
        meta: {
          title: "修改密码",
          showLink: false,
          keepAlive: false
        }
      }
    ]
  },
  {
    path: "/overview",
    name: "VisionOverview",
    component: Layout,
    redirect: "/overview/index",
    meta: {
      icon: "ri:dashboard-line",
      title: "监控总览",
      rank: 1
    },
    children: [
      {
        path: "/overview/index",
        name: "Overview",
        component: () => import("@/views/vision/Overview.vue"),
        meta: {
          title: "监控总览",
          keepAlive: false
        }
      }
    ]
  },
  {
    path: "/cameras",
    name: "VisionCameras",
    component: Layout,
    redirect: "/cameras/index",
    meta: {
      icon: "ri:vidicon-line",
      title: "视频管理",
      rank: 5
    },
    children: [
      {
        path: "/cameras/index",
        name: "Cameras",
        component: () => import("@/views/vision/Cameras.vue"),
        meta: {
          title: "视频管理",
          keepAlive: false
        }
      }
    ]
  },
  {
    path: "/monitor",
    name: "VisionMonitor",
    component: Layout,
    redirect: "/monitor/index",
    meta: {
      icon: "ri:live-line",
      title: "实时监控",
      rank: 4
    },
    children: [
      {
        path: "/monitor/index",
        name: "Monitor",
        component: () => import("@/views/vision/Monitor.vue"),
        meta: {
          title: "实时监控",
          keepAlive: false
        }
      }
    ]
  },
  {
    path: "/alert-desk",
    name: "VisionAlertDesk",
    component: Layout,
    redirect: "/alert-desk/index",
    meta: {
      icon: "ri:alarm-warning-line",
      title: "告警处置",
      rank: 2
    },
    children: [
      {
        path: "/alert-desk/index",
        name: "AlertDesk",
        component: () => import("@/views/vision/AlertDesk.vue"),
        meta: {
          title: "告警处置",
          keepAlive: false
        }
      }
    ]
  },
  {
    path: "/alerts",
    name: "VisionAlerts",
    component: Layout,
    redirect: "/alerts/index",
    meta: {
      icon: "ri:file-list-3-line",
      title: "告警查询",
      rank: 3
    },
    children: [
      {
        path: "/alerts/index",
        name: "Alerts",
        component: () => import("@/views/vision/Alerts.vue"),
        meta: {
          title: "告警查询",
          keepAlive: false
        }
      }
    ]
  },
  {
    path: "/dashboard",
    name: "VisionDashboard",
    component: Layout,
    redirect: "/dashboard/index",
    meta: {
      icon: "ri:bar-chart-box-line",
      title: "告警大屏",
      rank: 6
    },
    children: [
      {
        path: "/dashboard/index",
        name: "Dashboard",
        component: () => import("@/views/vision/Dashboard.vue"),
        meta: {
          title: "告警大屏",
          keepAlive: false
        }
      }
    ]
  },
  {
    path: "/models",
    name: "VisionModels",
    component: Layout,
    redirect: "/models/index",
    meta: {
      icon: "ri:cpu-line",
      title: "场景模型",
      rank: 7
    },
    children: [
      {
        path: "/models/index",
        name: "Models",
        component: () => import("@/views/vision/Models.vue"),
        meta: {
          title: "场景模型",
          keepAlive: false
        }
      }
    ]
  },
  {
    path: "/ops",
    name: "VisionOps",
    component: Layout,
    redirect: "/ops/index",
    meta: {
      icon: "ri:dashboard-2-line",
      title: "系统运维",
      rank: 8
    },
    children: [
      {
        path: "/ops/index",
        name: "Ops",
        component: () => import("@/views/vision/Ops.vue"),
        meta: {
          title: "系统运维",
          keepAlive: false
        }
      }
    ]
  },
  {
    path: "/settings",
    name: "VisionSettings",
    component: Layout,
    redirect: "/settings/profile",
    meta: {
      icon: "ri:settings-3-line",
      title: "系统设置",
      rank: 9,
      showLink: false
    },
    children: [
      {
        path: "/settings/profile",
        name: "SettingsProfile",
        component: () => import("@/views/account/SettingsHub.vue"),
        meta: {
          title: "系统设置",
          keepAlive: false,
          showLink: false
        }
      },
      {
        path: "/settings/password",
        name: "SettingsPassword",
        component: () => import("@/views/account/ChangePassword.vue"),
        meta: {
          title: "修改密码",
          keepAlive: false,
          showLink: false
        }
      },
      {
        path: "/settings/contacts",
        name: "Contacts",
        redirect: "/settings/profile?tab=contacts",
        meta: {
          title: "紧急联系人",
          keepAlive: false,
          showLink: false
        }
      },
      {
        path: "/settings/users",
        name: "Users",
        redirect: "/settings/profile?tab=users",
        meta: {
          title: "用户管理",
          keepAlive: false,
          showLink: false,
          roles: ["admin"]
        }
      }
    ]
  }
] satisfies Array<RouteConfigsTable>;
