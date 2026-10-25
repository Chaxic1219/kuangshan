# 矿山视界 · 本地 YOLO 视频分析

本机控制台：配置多路视频、绑定 YOLO/OpenCV 场景、多边形区域入侵报警。画面与推理均在本机完成，不上传云端。

```text
视频地址 → 截帧封面 → 选择场景 → 绘制区域 → 启动分析 → 事件记录
```

## 快速开始

1. 双击 `启动分析控制台.vbs`（无终端窗口；原 `.bat` 入口也会转为后台启动）
2. 打开 http://127.0.0.1:8765/
3. 添加视频 →「测试连接并截取封面」→ 选择场景 →（区域入侵需绘制区域）→ 保存 → 启动分析

也可选择本机演示视频（`kstool/mediamtx/videos/*.mp4`）。

控制台后台日志保存在 `data/console.log`；启动失败会弹出提示。重复启动会复用已运行的控制台。
普通编辑保存保留同一视频地址的 USB 设备关联；改为其他地址会清除旧关联，新接入以已验证的取帧结果建立关联。

## 模型与场景

| 模型 | 权重 | 场景 |
|---|---|---|
| 通用目标检测 | `weights/general.pt` | 人员、车辆、人员/车辆区域入侵 |
| 安全装备 | `weights/ppe.pt` | 未戴安全帽、未穿反光衣 |
| 火焰烟雾 | `weights/fire.pt` | 火焰、烟雾 |
| OpenCV | 无需权重 | 亮度异常 |

权重来源与校验见 `weights/manifest.json`、`weights/verified.json`。专项识别效果需用现场视频复核。

## 自检与冒烟

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe smoke_test.py
```

冒烟测试真实校验检测链路：使用含井下人员的素材（`camera12.mp4`）验证人员与区域
入侵、用 `camera5.mp4` 验证安全装备，并要求人员检出与入侵告警均大于 0 才算通过。

## 本机摄像头接入（USB/内置）

本机摄像头作为标准 RTSP 视频源，统一通过“＋ 添加视频”接入，没有独立批量接入页面。

1. 第 1 步填写名称；系统自动枚举服务所在电脑上的摄像头，只有一台可用设备时自动选中。
2. 点击“使用此摄像头并测试连接”，启动 USB 到 RTSP 的转换，自动填入视频地址并生成封面。
3. 按普通摄像头流程选择场景和模型、调整参数、绘制区域，然后保存配置。
4. 回到普通视频卡片点击“开始分析”。已有视频直接点击“配置”修改，避免重复添加。

也可以直接填写网络摄像头地址并测试连接。两种方式共用封面、区域、保存和分析逻辑。
枚举不打开采集；点击测试后才启动推流。测试失败不会创建视频记录；取消编辑也不会保存视频，
但已启动的 RTSP 推流会继续运行，可使用 `kstool\停止.bat` 停止，之后需重新启动本机推流再分析。
模拟流与本机流共用服务，会话冲突时不会自动切换或停止模拟流。

接口：`GET /api/cameras/devices` 枚举设备；`POST /api/cameras/live/prepare` 接收 `{deviceId}`，
返回普通导入所需的 `url`、`coverToken`、`cover`；最终统一通过 `/api/cameras/save` 保存。
原有 `live/adopt`、`live/batch` 接口保留兼容，控制台不再调用。写入接口沿用控制台令牌校验。

采集由 FFmpeg 从 dshow 读取并转码（H.264、`ultrafast`、无 B 帧、`yuv420p`、低码率），
推流进程由 MediaMTX 的 `runOnInit` 托管，启停与端口冲突检查复用 `stream_control.py` 的
作业对象机制。默认不指定输入分辨率和帧率，由驱动选择格式。管理 API 开启时，每路最多等待 12 秒出流，再用真实截帧验证；API 关闭时由截帧直接验证。多路失败时需等待各路超时结束。

限制：摄像头同一时刻只能被一个程序占用；多路 USB 摄像头建议分散到不同 USB 控制器；
转码占用 CPU，6 路并发上限照常适用；虚拟摄像头接入的是虚拟内容，注意别选错设备。

## 告警上报（可选）

配置环境变量后启用，未配置时仅本机展示、不对外发送：

| 变量 | 默认 | 说明 |
|---|---|---|
| `KUANGSHAN_ALERT_API_URL` | 空（禁用） | Java `POST /api/alerts` 地址 |
| `KUANGSHAN_ALERT_TIMEOUT_SECONDS` | 5 | 单次上报超时 |
| `KUANGSHAN_ALERT_UPLOAD_ROOT` | `D:\kuangshanai-uploads` | 证据图落盘根目录 |
| `KUANGSHAN_ALERT_UPLOAD_PREFIX` | `/uploads` | 返回给 Java 的 URL 前缀 |

`KUANGSHAN_ALERT_COOLDOWN_SECONDS` 已弃用：告警冷却统一由业务层（任务/区域/目标维度）
负责，桥接层不再按事件类型丢弃已接受事件；启动时若检测到该变量会提示并忽略。
检测没有跟踪编号时只能按类别冷却，无法区分同一类别下的不同目标，页面会注明该限制。

上报可靠性：待发送记录写入 SQLite outbox（`data/outbox/<视频>/outbox.db`），
`state` 为 `pending`（等待/重试）或 `dead`（需要人工处理）。超时、连接错误、429、5xx
按 1/2/5/10/30 秒退避保持 pending；其他 4xx、重定向、坏负载与证据缺失标记 dead，
不再自动重试。**服务重启后 pending 仍会补发**；停止分析时会先尽力发完再退出。
`alertCode` 跨运行稳定且**同一帧内每个目标各自独立编号**，Java 侧按唯一
`alert_code` 幂等去重。

事件记录页显示本次启动发送/失败尝试、持久化待发送与待处理数量，并列出 dead 记录，
可对指定行人工重试（`POST /api/alerts/retry`，列表 `GET /api/alerts/outbox`）；
对应视频未运行时只重新排队，并在下次启动分析后发送。outbox 表结构迁移幂等，
迁移前备份与回滚边界见 `backups/2026-10-17-batch5/migration-notes.md`。

事件记录页可直接**导出 CSV**（带 UTF-8 BOM，Excel 打开不乱码）。

## 准确率测量（可选）

准确率不能用“检出数量”代替。带标注样本时运行：

```powershell
python evaluate_accuracy.py template --video kstool/mediamtx/videos/camera5.mp4 --out dataset --count 30 --step 30
python evaluate_accuracy.py evaluate --model ppe --images dataset/images --annotations dataset/labels.jsonl
```

标注格式：`labels.jsonl` 每行
`{"image": "frame_00001.jpg", "boxes": [{"label": "NO-Hardhat", "bbox": [x1, y1, x2, y2]}]}`，
bbox 为 0~1 归一化 xyxy，label 使用模型类别名。报告按标签统计 TP/FP/FN 与
precision/recall，固定标记 `assessment=measurement_only`：没有业务阈值和现场标注集时
只报告测量结果，不判定准确率是否达标。

无标注时只能测量检出数量：`python evaluate_accuracy.py measure --model general --images <dir>`。
报告不会写“准确率合格”。人员、装备、烟火应分别用现场正负样本统计漏报与误报。

## 运维与验收

运维、备份与回滚步骤见 [../docs/operations.md](../docs/operations.md)。联合验收脚本：

```powershell
python acceptance.py --minutes 2               # 短时：默认 6 路分析（含 2 路模拟流）
python acceptance.py --minutes 2 --analyze 4   # 低配机器可减少分析路数
python acceptance.py --minutes 480             # 8 小时稳定性（固定机器）
```

验收计划、预期可观察行为与 8 小时清单见 [../docs/acceptance.md](../docs/acceptance.md)，
本次短时结果见 [../docs/acceptance-report-2026-10-17.md](../docs/acceptance-report-2026-10-17.md)。
报告写入 `runs/acceptance-<时间>/report.json` 与 `report.md`。

## 容量与稳定性

| 变量 | 默认 | 说明 |
|---|---|---|
| `KUANGSHAN_MAX_RUNNING` | 6 | 同时运行的视频上限，超出时明确拒绝 |
| `KUANGSHAN_WATCHDOG_SECONDS` | 30 | 心跳序号停止前进超过该秒数则重建该路进程 |
| `KUANGSHAN_INITIAL_TIMEOUT_SECONDS` | 60 | 进程启动后首个心跳的期限，超时视为初始化卡死 |

每路视频是一个 Windows 作业对象（Job Object）：控制台异常退出或关闭最后句柄时，该路进程组随之终止；同一数据目录只允许一个控制台实例（命名互斥量），不同数据目录可并行。故障后按 1/2/5/10/30 秒退避自动恢复，十分钟内最多自动重启 5 次，超限进入 `failed` 并等待人工启动；`/api/state` 的 `lifecycle`（starting/active/stopping/backoff/failed/stopped）、`lastFailure`、`nextRestartIn` 会返回给页面，暂停状态不会被展示成正常运行。

存储维护：可再生运行文件由 `maintenance.py` 每十分钟有界清理（每批最多 100 个候选）：`latest` 引用帧始终保护，其余帧至少保留 60 秒；已结束运行/冒烟目录保留 7 天；无引用封面与 outbox 截图保留 24 小时；事件数据库、证据图、权重与源视频不自动删除。`results.jsonl`/`events.jsonl` 单文件 20 MiB，最多保留 5 个轮转文件。数据目录（及启用上报时的上传目录卷）低于 5 GiB 提示、低于 1 GiB 拒绝启动新分析并停止现有任务，需清理后人工重新启动。文件被占用时跳过并记录；目录穿越、符号链接与 junction 拒绝删除。预览候选：`python maintenance.py --work data --runs runs`（默认只预览，`--execute` 才删除）。

每路视频一个进程并各自加载权重，并发数直接决定内存与 CPU 占用；卡死时只重建
受影响的那一路，其他路继续运行（同路多个场景会一起短暂重启，这是首版的隔离边界）。

实测单路 `person` 任务约占 **333 MB 内存**（含 YOLO 权重与取流缓冲），
按此推算默认上限 6 路约需 2 GB。`/api/state` 的 `capacity` 会汇总当前运行中
任务的 CPU/内存，可据此调整 `KUANGSHAN_MAX_RUNNING`。

## 主要文件

- `camera_source.py` 本机摄像头接入（dshow 设备枚举、live 配置生成、受管段增删）
- `live_cameras.py` / `live-cameras.js` 多路设备选择、旧配置复用与批量启动面板
- `maintenance.py` 可再生运行文件清理（预览/执行、引用保护、低空间检测）
- `evaluate_accuracy.py` 离线准确率测量（标注模板、漏报/误报统计，不判定合格）
- `catalog.py` 视频配置、场景与模型目录
- `detectors.py` YOLO / OpenCV 场景适配
- `regions.py` 多边形校验与进入状态
- `service.py` 取流、调度、结果与证据落盘
- `console.py` / `console.html` / `console.js` / `console.css` 本机管理台
- `capture.py` 封面截帧
- `alerts.py` 可选 Java 预警上报（SQLite outbox：pending/dead、退避重试、人工重试、幂等编号）
- `weights/` 本地模型权重
- `kstool/` 演示视频与 MediaMTX/FFmpeg 推流工具（素材根目录，可用 `KUANGSHAN_MEDIA_ROOT` 指向其他位置）

## 素材说明

`kstool/mediamtx/videos/` 中并非每路都含人员：

| 素材 | 内容 | 适用场景 |
|---|---|---|
| `camera5.mp4` | 综采工作面，多名矿工（安全帽/反光衣） | 人员、安全装备、区域入侵 |
| `camera12.mp4` | 机电硐室，多人作业 | 人员、区域入侵 |
| `camera1/2/9.mp4` | 采掘机械特写，**画面内无人** | 仅亮度、机械类场景 |

用无人素材验证「人员检测」会永远得到 0 检出，请按上表选素材。

