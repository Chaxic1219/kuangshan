# 视联洞察 · 本地视频智能分析

本机运行的多路视频分析控制台：配置视频源、绑定 YOLO/OpenCV 场景、绘制区域、
启动分析、查看事件与证据。画面与推理都在本机完成，不上传云端。

本文重点说明**如何启动**。模型、接口、运维与验收细节见
[analysis-service/README.md](analysis-service/README.md)。

---

## 一、最快启动

### macOS

```bash
cd "/path/to/kuangshan new"        # 替换为项目实际路径
./启动项目.command --mode live --autostart
```

没有可执行权限时先执行 `chmod +x 启动项目.command`。也可以直接用 Python：

```bash
python3 start.py --mode live --autostart
```

### Windows

在 PowerShell 或 cmd 中：

```bat
cd /d "D:\path\to\kuangshan new"
启动项目.bat --mode live --autostart
```

或者双击 `启动项目.bat`（不带参数时默认 `--mode lite`）。

启动完成后：

- 新版 Vue3 后台：**http://127.0.0.1:8765/ui/**
- 旧版控制台：**http://127.0.0.1:8765/legacy/**（访问 `/` 会自动跳转到 `/ui/`）

> 首次运行会自动创建虚拟环境、安装依赖，耗时较长（PyTorch 等约 2 GB），
> 之后启动只需几秒。运行期间请保持这个终端窗口打开，关闭窗口会停止全部组件。

---

## 二、启动模式

演示流和本机摄像头共用同一个 MediaMTX（RTSP 8560），**同一时刻只能选一种模式**。

| 模式 | 启动内容 | 适用场景 | 地址 |
|---|---|---|---|
| `--mode lite`（默认） | 8 路演示流 `camera1~camera8` + 控制台 | 演示、低配机器 | `rtsp://127.0.0.1:8560/camera1`… |
| `--mode full` | 18 路演示流 `camera1~camera18` + 控制台 | 完整演示、验收 | `rtsp://127.0.0.1:8560/camera1`… |
| `--mode live` | 本机摄像头流 `livecamN` + 控制台 | 使用 USB / 内置摄像头 | `rtsp://127.0.0.1:8560/livecam1`… |
| `--mode none` | 只启动控制台 | 只想打开管理界面 | 无 RTSP 流 |

**本机摄像头只在 `--mode live` 下存在。** 在 `lite/full` 下打开
`rtsp://127.0.0.1:8560/livecam1` 不会有任何内容，这是正常的。

### 常用参数

| 参数 | 说明 |
|---|---|
| `--mode full\|lite\|live\|none` | 选择启动模式，默认 `lite` |
| `--autostart` | 启动后自动开始所有已配置检测场景的视频 |
| `--port 8765` | 控制台端口 |
| `--setup` | 强制重新安装 Python 依赖 |
| `--no-download` | 缺少 MediaMTX/FFmpeg 时只报错，不自动下载 |

`--autostart` 会按模式过滤视频源：`lite/full` 跳过 `livecam*`，`live` 跳过
`cameraN` 演示流；同一地址的重复视频只启动一次。

---

## 三、首次启动会做什么

`start.py` 按顺序完成：

1. **检查 Python 环境**
   - Windows 使用 `analysis-service/.venv/Scripts/python.exe`
   - macOS 使用 `analysis-service/.venv/bin/python`
   - Linux 使用 `analysis-service/.venv-linux/bin/python`
   - 不存在则创建，并 `pip install -r analysis-service/requirements.txt`
2. **检查推流工具**
   - MediaMTX：缺失时按平台从 GitHub 官方 release 自动下载到 `kstool/mediamtx/bin/`
   - FFmpeg：项目已内置 `kstool/mediamtx/bin/darwin-arm64/ffmpeg`（macOS）与 Windows 下载兜底；
     macOS 也可回退到系统 `ffmpeg`（`brew install ffmpeg`）
3. **启动演示流 / 摄像头流**（`--mode none` 时跳过）
4. **启动控制台** `127.0.0.1:8765`
5. **可选自动启动分析**（`--autostart`）

### 环境要求

- Python 3.10+（推荐 3.11）
- Windows 10+ / macOS 12+（本机摄像头仅支持 Windows 与 macOS）
- 磁盘：依赖与权重约 3～5 GB；数据盘剩余空间低于 1 GiB 时拒绝启动新分析
- 内存：单路分析约 350～400 MB，默认最多同时 6 路
- FFmpeg：macOS 使用仓库内置的 darwin 版本；Windows 可由脚本自动下载

---

## 四、启动后验证

### 1. 控制台与前端

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/        # 302，跳转 /ui/
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/ui/     # 200
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/legacy/ # 200
```

### 2. 在线流列表

```bash
curl -s http://127.0.0.1:9997/v3/paths/list
```

- `lite`：`camera1`～`camera8`
- `full`：`camera1`～`camera18`
- `live`：`livecam1`…

### 3. 直接检查视频流

```bash
ffprobe -v error -rtsp_transport tcp -select_streams v:0 \
  -show_entries stream=width,height -of default=nw=1 \
  rtsp://127.0.0.1:8560/livecam1
```

正常情况下应输出 `width=1280`、`height=720`。

### 4. 查看任务状态

```bash
curl -s http://127.0.0.1:8765/api/state
```

或在页面上「视频管理」查看每路的 `state`、`results`、`lastFailure`。

---

## 五、停止

- 在运行 `start.py` 的终端按 **Ctrl+C**：依次停止分析、控制台、MediaMTX 与 FFmpeg
- 直接关闭终端窗口也可以：`start.py` 会处理 SIGHUP，通常几秒内清理完毕
- 如果终端被强制退出、或仍有残留进程，运行一键停止：

```bash
./停止项目.command          # macOS
停止项目.bat                # Windows
```

- 或发送 SIGTERM：

```bash
kill -TERM <start.py 的 PID>
```

- Windows：关闭窗口或在任务管理器结束 `python.exe` / `mediamtx.exe`
- 只停演示流/摄像头流、保留控制台：

```bash
python kstool/mediamtx/stream_control.py stop
```

停止后 8560 / 8765 端口会释放；若提示端口占用，说明还有旧实例没退干净，执行
`./停止项目.command` 兜底。

> RTSP 端口使用 **8560**（不是常见的 8554），因为 8554 可能被其他程序占用。
> 如需修改，请同时改 `kstool/mediamtx/mediamtx*.yml` 的 `rtspAddress` 和
> `runOnInit` 地址、`analysis-service/data/cameras.json` 里的摄像头 URL。

---

## 六、macOS 摄像头权限（重点）

macOS 会把“访问摄像头”归属到**启动控制台的那个应用**，而不是 ffmpeg 本身。

- **必须从 Terminal 或 iTerm 启动**（双击 `启动项目.command` 也是打开 Terminal）
- 首次使用会弹出系统授权框，选择“允许”
- 若曾拒绝：系统设置 → 隐私与安全性 → 摄像头 → 勾选 Terminal / iTerm

以下宿主应用通常**无法**授权摄像头，因为它们的签名缺少
`com.apple.security.device.camera` 权限，系统会直接拒绝且不弹窗：

- VS Code / Cursor 等编辑器的内置终端（取决于其 Helper 进程归属）
- DSH、Codex++ 等第三方宿主应用
- 任何从这些应用里启动的 `python start.py`

在这类环境里，控制台能枚举设备，但点“使用此摄像头并测试连接”会超时并报权限错误。
解决办法只有一个：改用 Terminal/iTerm 启动整套服务。

临时绕行：先在 Terminal 里执行

```bash
python kstool/mediamtx/stream_control.py start --mode live
```

再在任意控制台的“视频地址”里手动填 `rtsp://127.0.0.1:8560/livecam1`
并点“测试连接并截取封面”。

---

## 七、Windows 注意事项

- 本机摄像头通过 FFmpeg `dshow` 枚举和采集，设备名来自驱动
- 首次启动如弹出 Windows 防火墙提示，允许专用网络即可；控制台只监听
  `127.0.0.1`，MediaMTX 默认监听所有网卡，可按需在防火墙中限制
- 需要 Python 3.10+ 并加入 PATH；脚本会优先使用项目内 `.venv`
- 缺少 FFmpeg 时脚本会下载官方 essentials 构建到 `kstool/mediamtx/bin/ffmpeg_bin/`
- 关闭控制台窗口时，Windows 作业对象会带走该实例派生的分析进程

---

## 八、常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 打开 `livecam1` 没有任何内容 | 当前是 `lite/full` 演示流模式 | 停止后改用 `--mode live` |
| 摄像头列表可见，但测试连接失败 | 宿主应用没有摄像头权限（见第六节） | 从 Terminal/iTerm 启动 |
| 摄像头画面竖屏、比例不对、很糊 | 采集命令缺少 `-video_size`，AVFoundation 选了 1080×1920 | 重新保存摄像头，或确认配置里有 `-video_size 1280x720` |
| 提示端口 8765 已被占用 | 已有控制台在运行 | 先 Ctrl+C 或结束旧进程 |
| 提示 8560 端口冲突 | 已有演示流/摄像头流在运行 | 先 `stream_control.py stop`，再切换模式 |
| 首次启动很慢 | 正在下载 PyTorch 等依赖 | 保持网络，等待安装完成 |
| 分析点了没反应 | 未绑定场景、模型未就绪、达到 6 路上限 | 检查场景与 `lastFailure`，先停其他视频 |
| 关闭终端后服务全停了 | `start.py` 是前台进程，关闭窗口会清理 | 需要长期运行请使用独立终端或终端复用器 |
| 关闭终端后仍有 `mediamtx` / `ffmpeg` / `service.py` 在跑 | 终端被强制退出，信号没来得及处理 | 运行 `./停止项目.command`，按项目路径精确清理 |
| 页面打不开 | 控制台未启动或端口被改 | 查看 `analysis-service/data/console.log` |
| 磁盘不足无法启动分析 | 数据盘剩余低于 1 GiB | 清理 `analysis-service/data/runtime` 或扩容 |
| `/ui/` 返回 404 | 前端未构建或 `frontend_hosting.py` 缺失 | 确认 `frontend_thin/dist/index.html` 存在，且 `analysis-service/frontend_hosting.py` 已提交 |
| 启动报 `No such file: .venv/bin/python` | 拷贝了 Windows 的 `.venv` | 重新执行 `python3 -m venv analysis-service/.venv` 并安装依赖 |
| 启动报 `Permission denied: .../bin/darwin/mediamtx` | 二进制丢失可执行位 | `chmod +x kstool/mediamtx/bin/darwin*/mediamtx kstool/mediamtx/bin/darwin*/ffmpeg` |

---

## 九、不用一键脚本时的分步启动

```bash
# 1) 演示流（或把 lite 换成 full / live）
python kstool/mediamtx/stream_control.py start --mode lite

# 2) 控制台
python analysis-service/console.py --port 8765

# 3) 停止演示流
python kstool/mediamtx/stream_control.py stop
```

Windows 把 `python` 换成 `analysis-service\.venv\Scripts\python.exe`。

---

## 十、组件与目录

```text
start.py                         一键启动：环境、演示流、控制台、可选分析
启动项目.command / .bat           双击启动入口（默认 lite）
analysis-service/
  console.py                     控制台 HTTP 服务（8765）
  frontend_hosting.py            /ui/ 静态资源托管
  alert_dashboard.py             告警大屏聚合接口
  service.py                     单路分析进程（YOLO/OpenCV）
  camera_source.py               本机摄像头：dshow / avfoundation 枚举与配置
  hostplat/                      二进制解析、摄像头命令、跨平台进程管理
  process_scope.py               进程组管理（Windows 作业对象 / POSIX 进程组）
  weights/                       YOLO 权重
  data/                          配置、封面、事件库、运行目录、日志
frontend_thin/                   Vue3 后台（pure-admin-thin 6.2.0）
frontend_thin/dist/              已构建产物（/ui/ 直接托管）
kstool/mediamtx/
  bin/darwin-arm64/              macOS MediaMTX / FFmpeg（已入库）
  bin/                           Windows 二进制（不入库，可自动下载）
  mediamtx.yml                   full 模式：18 路演示流
  mediamtx-lite.yml              lite 模式：8 路演示流
  mediamtx-live.yml              live 模式：本机摄像头受管段
  stream_control.py              推流安全启停（端口检查、进程回收）
docs/                            运维、验收文档
```

运行关系：

```text
start.py
 ├─ MediaMTX :8560 ── ffmpeg × N（演示流）
 │                 └─ ffmpeg（本机摄像头 livecamN）
 └─ console.py :8765
      └─ service.py × 路数（每路一个进程，各自加载模型）
```

### 日志位置

| 组件 | 日志 |
|---|---|
| 控制台 | `analysis-service/data/console.log` |
| 推流管理进程 | `kstool/mediamtx/runtime/manager.log` |
| MediaMTX 实例 | `kstool/mediamtx/runtime/<实例>/mediamtx.log` |
| 单路分析 | `analysis-service/data/runtime/<视频ID>/<运行ID>/service.log` |

---

## 十一、更多文档

- [analysis-service/README.md](analysis-service/README.md)：模型、场景、接口、告警、准确率测量
- [kstool/mediamtx/README.md](kstool/mediamtx/README.md)：推流工具与摄像头接入细节
- [docs/operations.md](docs/operations.md)：运维、备份与回滚
- [docs/acceptance.md](docs/acceptance.md)：验收计划与清单
