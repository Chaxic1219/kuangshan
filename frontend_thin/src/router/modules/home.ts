const Layout = () => import("@/layout/index.vue");

/** 隐藏模板首页；登录后落到值班「监控总览」。 */
export default {
  path: "/",
  name: "Home",
  component: Layout,
  redirect: "/overview/index",
  meta: {
    icon: "ep/home-filled",
    title: "首页",
    rank: 0,
    showLink: false
  },
  children: [
    {
      path: "/welcome",
      name: "Welcome",
      component: () => import("@/views/welcome/index.vue"),
      meta: {
        title: "首页",
        showLink: false
      }
    }
  ]
} satisfies RouteConfigsTable;
