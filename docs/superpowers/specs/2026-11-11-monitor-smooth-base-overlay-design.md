# 实时监控放大页：流畅底流 + 识别框叠层

日期：2026-11-11  
状态：待实现  
范围：仅「实时监控」点击封面后的放大播放页

## 背景与问题

放大页当前使用 `AnnotatedLive`，播放分析进程写出的带框 MJPEG（`/live/{cameraId}`）。画面更新节奏与推理绑定：人员区域入侵使用较重的 `factory_person` 时，整幅预览一起卡顿。安全帽等轻量模型则相对流畅，造成「同一套监控、不同场景观感差很大」。

用户需要的观感是：**视频本身连贯；识别框可以慢，但不要拖垮底流**。

## 目标

1. 放大页优先播流畅底流（本机 WebRTC 或本地原视频）。
2. 检测框 / 布控区域以 SVG 叠在底流之上，数据来自 `camera.latest`。
3. 检测落后时显示「检测延迟 Xs」，避免误以为框与画面严格同步。
4. 底流不可用或播放失败时，自动回退到现有带框 MJPEG，保证仍有画面。
5. 不改封面网格、告警证据页、后端推流与推理管线。

## 非目标

- 不替换 / 压缩 `factory_person` 权重（可后续单独优化）。
- 不改造封面网格为多路流畅预览。
- 不改告警证据卡（`DetectionFrame`）行为。
- 不引入新的推流协议或后端叠加服务。

## 方案选择

采用 **新组件 `SmoothLive`**，在 Monitor 放大页替换 `AnnotatedLive` 引用：

| 方案 | 结论 |
|---|---|
| 新组件 SmoothLive（底 LivePlayer + SVG 叠层，失败回退 AnnotatedLive） | **采用**：边界清晰，证据页不受影响 |
| 改造 AnnotatedLive 兼做两种模式 | 否：职责变杂 |
| 改造 DetectionFrame 共用 | 否：易误伤证据展示 |

## 架构

```text
Monitor 放大页（playing && running）
  └─ SmoothLive(camera)
       ├─ 若 camera.preview.kind ∈ {webrtc, video}
       │    且 LivePlayer 未失败
       │    ├─ LivePlayer（流畅底流）
       │    └─ SVG 叠层（区域 + detections + 延迟角标）
       └─ 否则
            └─ AnnotatedLive（现有 MJPEG 带框图）
```

后端已有 `preview_for(camera)`：本地文件 → `video`；本机 MediaMTX RTSP → `webrtc`；其它 → `frames`。本设计只消费该字段，不改生成逻辑。

## 组件与行为

### SmoothLive

- 输入：`camera`（含 `preview`、`latest`、`liveStream`、`regions` / 任务绑定区域）。
- 布局：与现 `AnnotatedLive` 单路放大一致（宽度优先、真实宽高比、`max-height: 70vh`）。
- 底流：复用现有 `LivePlayer`。
- 叠层：SVG `viewBox="0 0 1 1"` + `preserveAspectRatio="none"`，坐标系与 `DetectionFrame` 一致（归一化 bbox / 区域点）。
- 检测数据：取 `Object.values(camera.latest)` 中 `capturedAt` 最新的一条；区域用该记录的 `regions`（控制台已按当前配置过滤）。
- 样式：框描边 / 标签文案复用 `frame.mjs` 的 `detectionStroke`、`formatDetectionCaption`。

### 延迟角标

- 计算：`Date.now() - Date.parse(capturedAt)`。
- 展示：「检测延迟 Xs」（或毫秒级文案，保持简洁）。
- 阈值建议：&lt;1s 可弱化或隐藏；≥1s 常显；≥3s 警告色。
- 无 `capturedAt` / 无 latest：不显示延迟，可显示「等待检测结果」。

### 失败回退

触发条件（任一）：

1. `preview` 缺失，或 `kind` 不是 `video` / `webrtc`；
2. `LivePlayer` 发出错误（WebRTC/原视频加载失败）。

行为：切换到 `AnnotatedLive`；可短暂或常驻提示「已回退分析预览」。底流恢复成功时可再切回流畅模式（实现时以简单状态机为准：失败后保持回退直至该路 cameraId 变更或用户离开放大页，避免抖动；若实现成本低，也可在 preview 重新可用时自动恢复）。

推荐默认：**失败后保持回退，直到离开放大页或切换摄像头**，避免 WebRTC 间歇失败导致闪烁。

### Monitor 接入

- `Monitor.vue` 放大区：`running` 时渲染 `SmoothLive` 替代 `AnnotatedLive`。
- 分析未运行：仍显示「分析未运行，暂无实时流」（不变）。
- 现有状态条（目标数、延迟、推理耗时等）保留。

## 数据流

```text
摄像头 / 本地文件
  → MediaMTX WebRTC 或 /preview/{id}     （流畅底流，与推理无关）
分析进程
  → latest + live.jpg                   （检测结果；MJPEG 仅作回退）
前端 store 轮询 /api/state
  → SmoothLive 读 preview + latest
```

## 错误处理

| 情况 | 行为 |
|---|---|
| 本机摄像头 + live 模式正常 | 流畅 WebRTC + 叠框 |
| 本地 mp4 素材 | `/preview` 循环播 + 叠框 |
| 远程 RTSP（preview=frames） | 直接 AnnotatedLive |
| WebRTC 失败 | 回退 AnnotatedLive + 提示 |
| 分析运行但尚无 latest | 底流照常；叠层空；提示等待检测 |
| 页面隐藏 | 沿用 LivePlayer / AnnotatedLive 既有停播或断流策略，避免后台空耗 |

## 测试与验收

1. `--mode live` 下本机摄像头放大页：底流目视流畅，切换到区域入侵后视频不随模型变慢而整屏顿挫。
2. 区域多边形与检测框可见；框更新可慢于视频。
3. 人为拖慢或等待后，角标出现「检测延迟 Xs」。
4. 停止推流或破坏 WHEP 后，自动回退 MJPEG 带框图。
5. 封面网格、告警证据页行为不变。
6. `frontend_thin`：`npm run build` 通过。

可选单测：SmoothLive 对「无 preview / LivePlayer 错误」选择回退分支的纯逻辑（若抽成小函数）。

## 风险与后续

- **音视频与检测时间轴本就不同步**：角标是产品诚实说明，不是精确对帧标定。
- **多路同抢一台本机摄像头**：仍可能拖慢推理；本设计只解「预览绑死推理」问题。
- 后续若要封面网格也流畅，可复用 SmoothLive，但默认多路 WebRTC 成本高，需另开需求。
- 人员模型体积优化可并行，但不阻塞本需求。
