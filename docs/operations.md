# 运维手册（analysis-service）

本手册面向部署与日常运维，配套设计文档 `docs/superpowers/specs/2026-10-17-reliability-design.md`。

## 1. 目录与责任边界

| 路径 | 内容 | 处理方式 |
|---|---|---|
| `data/cameras.json` | 视频配置（含封面引用） | 人工维护，纳入备份 |
| `data/events.db` | 本地告警事件库 | 业务证据，不自动删除；纳入备份 |
| `data/outbox/<视频>/` | 待发送队列与证据截图 | `pending`/`dead` 状态；迁移前备份 |
| `data/runtime/` | 每次运行的帧、结果、日志 | 可再生；已结束目录保留 7 天 |
| `data/covers/` | 封面图 | 可再生；无引用 24 小时后清理 |
| `data/evidence/` | 事件证据图 | 业务证据，不自动删除 |
| `runs/` | 冒烟与验收产物 | 可再生；保留 7 天 |
| `weights/` | 模型权重 | 不自动删除 |
| `kstool/` | 模拟流、素材与启停脚本 | 素材端，不自动清理 |

## 2. 环境变量总表

| 变量 | 默认 | 说明 |
|---|---|---|
| `KUANGSHAN_MAX_RUNNING` | 6 | 同时运行的视频上限，超出时明确拒绝 |
| `KUANGSHAN_WATCHDOG_SECONDS` | 30 | 心跳序号停止前进超过该秒数则重建该路进程 |
| `KUANGSHAN_INITIAL_TIMEOUT_SECONDS` | 60 | 启动后首个心跳期限，超时视为初始化卡死 |
| `KUANGSHAN_MEDIA_ROOT` | `<项目根>/kstool` | 视频素材根目录；相对路径按项目根解析 |
| `KUANGSHAN_ALERT_API_URL` | 空（禁用） | Java `POST /api/alerts` 地址；非本机必须 HTTPS |
| `KUANGSHAN_ALERT_TIMEOUT_SECONDS` | 5 | 单次上报超时（1～30 秒） |
| `KUANGSHAN_ALERT_UPLOAD_ROOT` | `D:\kuangshanai-uploads` | 证据图落盘根目录（Java 读取的卷） |
| `KUANGSHAN_ALERT_UPLOAD_PREFIX` | `/uploads` | 返回给 Java 的 URL 前缀 |

`KUANGSHAN_ALERT_COOLDOWN_SECONDS` 已弃用：告警冷却由业务层统一负责，启动时若检测到该变量会提示并忽略。

## 3. 启动、停止与故障排查

```powershell
python console.py --port 8765                 # 默认数据目录 data
python console.py --port 8766 --data D:\test  # 独立数据目录（可并行）
```

- 页面：`http://127.0.0.1:8765`；停止：`Ctrl+C` 或关闭窗口，作业对象会回收全部分析进程。
- 同一数据目录只允许一个控制台实例（命名互斥量）；不同数据目录可以并行。
- `/api/state` 关键字段：`lifecycle`（starting/active/stopping/backoff/failed/stopped）、`lastFailure`、`nextRestartIn`、`capacity.storage`。
- 单路日志：`data/runtime/<视频>/<启动>/<运行>/<视频>/events.jsonl`、`results.jsonl`；单文件 20 MiB，最多 5 个轮转文件。
- 常见故障：

| 现象 | 含义 | 处理 |
|---|---|---|
| `failed` | 十分钟内自动重启超过 5 次 | 查看 `lastFailure` 与事件日志，修复后人工启动 |
| `backoff` | 崩溃后等待退避重试 | 等待 `nextRestartIn`，持续 backoff 需排查 |
| `stopping` | 正在回收进程 | 等待；超时由控制台强制终止 |
| `capacity.storage=blocked` | 磁盘低于 1 GiB | 清理后人工重新启动分析 |
| 告警 `dead` | 4xx/重定向/坏负载/证据缺失 | 页面人工重试或修复配置 |

## 4. 告警队列

- 状态：`pending`（等待/重试）、`dead`（人工处理）；超时/连接错误/429/5xx 保持 pending 退避。
- 接口：`GET /api/alerts/outbox`、`POST /api/alerts/retry`；对应视频未运行时重试只排队，下次启动分析后发送。
- 迁移前备份与回滚边界见 `backups/2026-10-17-batch5/migration-notes.md`。
- 回滚：先停止全部发送者，再恢复整套迁移前数据库备份；**旧程序不能直接读取含 dead 的新队列**，否则会重发这些记录。

## 5. 存储维护与低空间

```powershell
python maintenance.py --work data --runs runs            # 预览候选（默认，不删除）
python maintenance.py --work data --runs runs --execute  # 执行删除（有界批次）
```

- 保护规则：latest 引用帧始终保护；普通帧 60 秒；运行/冒烟目录 7 天；无引用封面与 outbox 截图 24 小时；数据库、证据、权重、源视频不自动删除。
- 控制台每十分钟自动执行一次有界清理（每批最多 100 个候选）；引用查询失败时停止本批。
- 数据卷（启用上报时还包括上传目录卷）低于 5 GiB 提示、低于 1 GiB 拒绝启动新分析并停止现有任务；空间恢复后需人工重新启动。

## 6. 发布前备份与回滚

发布前至少保存：

1. 代码版本：`git rev-parse HEAD`（或打 tag）；
2. 数据库：`data/events.db` 与 `data/outbox/*/outbox.db`（用 SQLite backup API 或停服务后复制）；
3. 配置：`data/cameras.json`、`kstool/mediamtx/*.yml`、环境变量清单；
4. 验收报告路径。

回滚顺序：停止全部分析 → 恢复数据库与配置 → 切回上一代码版本（`git checkout <commit>`）→ 重启控制台 → 运行冒烟与短时验收。

**清理不可逆**：`maintenance.py --execute` 删除的运行帧、封面与 outbox 截图不可恢复（可再生）；回滚前先确认预览与保护规则，不要通过删除业务证据腾空间。

## 7. 验收与准确率

```powershell
python acceptance.py --minutes 2        # 短时：6 路含模拟流、至少 4 路分析
python acceptance.py --minutes 480      # 8 小时稳定性（固定机器）
python evaluate_accuracy.py template --video kstool/mediamtx/videos/camera5.mp4 --out dataset
python evaluate_accuracy.py evaluate --model ppe --images dataset/images --annotations dataset/labels.jsonl
```

验收计划与结果见 `docs/acceptance.md`；准确率没有现场标注集时只报告测量结果，不判定合格。
