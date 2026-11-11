import { defineFakeRoute } from "vite-plugin-fake-server/client";

/** 不返回模板权限演示菜单，侧栏只保留静态视觉业务路由。 */
export default defineFakeRoute([
  {
    url: "/get-async-routes",
    method: "get",
    response: () => ({
      success: true,
      data: []
    })
  }
]);
