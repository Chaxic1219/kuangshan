# 跨平台通用方案（优先 macOS）

日期：2026-10-25  
基线：当前 Windows 本机控制台 + MediaMTX/FFmpeg dshow + Job Object  
状态：已实施（2026-10-25）；darwin 二进制入库  

## 目标

同一仓库在 **Windows / macOS / Linux** 上提供同一套业务能力；**一期优先把 macOS 主路径跑通**，Windows 行为不回退，Linux 预留接口。

「网页选本机摄像头」语义不变：选的是**运行分析服务的那台机器**上的设备。

## 非目标（一期）

- 浏览器采集用户电脑 USB 再推到远端服务器推理  
- 重写 YOLO / 告警 / Pure Admin 业务  
- 统一 GPU 抽象（一期 CPU 一致即可）  
- 完整 Linux USB 打磨（二期）

## 架构

```text
frontend_thin  →  console HTTP  →  Controller（业务）
                                      ↓
                              platform/ 适配层
                         ┌──────┼──────┐
                    process  camera  binaries
                    lock     ffmpeg  mediamtx
```

新增目录（建议）：

```text
analysis-service/platform/
  __init__.py          # 导出 ProcessScope, SingleInstance, resolve_bin, camera API
  process_win.py       # 现有 Job Object（迁入）
  process_posix.py     # macOS/Linux：setsid + 进程组终止
  lock.py              # 文件锁（全平台）；Win 可叠加原互斥量
  camera.py            # 按平台分发 list_devices / build_push_command
  binaries.py          # 解析 mediamtx/ffmpeg 路径
kstool/mediamtx/bin/
  windows/             # 现有 exe 迁入或保留兼容链接
  darwin/              # mediamtx + ffmpeg（Apple Silicon / Intel 说明）
  linux/               # 二期
```

Controller / `stream_control.py` / `camera_source.py` **只依赖适配层**，禁止继续散落 `IS_WINDOWS` 业务分支（测试与薄封装除外）。

## 平台能力矩阵

| 能力 | Windows | macOS（一期） | Linux（二期） |
|------|---------|---------------|---------------|
| 本地 mp4 / 远程 RTSP 分析 | 保持 | 必须 | 预留 |
| 启停进程 + 看门狗 | Job Object | 进程组 | 同 posix |
| 单实例 | 互斥量 + 文件锁 | 文件锁 | 文件锁 |
| USB 摄像头 | dshow | avfoundation | v4l2 |
| MediaMTX / WebRTC 预览 | 现有 | darwin 二进制 | linux 二进制 |
| 启动方式 | `.vbs`/`.bat` 保留 | `launch.sh` / `python -m` | systemd 说明 |

## macOS 一期验收标准

1. 在 Apple Silicon 或 Intel Mac 上：创建 venv、安装依赖、启动控制台，打开 `/ui/` 可登录。  
2. 添加本地演示 mp4 或远程 RTSP，保存、启动分析，监控页有带框画面（或至少有检测截帧）。  
3. 运维页可看状态并对该路启动 / 强制重启。  
4. （若本机有摄像头）设备列表可枚举；「使用此摄像头」可生成 live RTSP 并完成封面探测。  
5. Windows 回归：现有 `unittest` 主路径与 Job Object 专项仍通过。

## 关键设计细节

### 1. 进程作用域（ProcessScope）

统一接口（与现有 FakeScope / Controller 调用对齐）：

- `main_alive()` / `active_process_count()` / `wait_for_active` / `wait_for_zero` / `terminate` / `close`

**posix**：`subprocess.Popen(..., start_new_session=True)`，`terminate` 时对整个进程组 `os.killpg`；关闭控制台时尽力回收子进程（文档注明：不如 Job Object 的「句柄关闭必杀」硬，但运维页强制重启 + 看门狗可补）。

### 2. 摄像头（camera）

- `list_camera_devices()`：按平台调用 ffmpeg 枚举并解析 stderr。  
- `build_push_command(device, path_name, ...)`：生成 **相对 bin 根** 的 ffmpeg 命令行，供 MediaMTX `runOnInit`；禁止写死 `.exe`。  
- macOS 示例形态：`-f avfoundation -i "<index或名称>"`（具体索引规则在实现时用真机校准，并写入 README）。

### 3. 二进制

- `resolve("mediamtx")` / `resolve("ffmpeg")`：`platform.system()` + 可选 `uname -m`。  
- 文档写明：darwin 二进制需自行下载放入 `bin/darwin/`（或脚本 `bootstrap_darwin.sh`），不强制把大文件提交进 git（可用 `.gitkeep` + 校验清单）。

### 4. 启动与本机访问

- 保留 Windows 启动器；Mac：`analysis-service/launch_console_posix.py` + `启动分析控制台.command`。  
- 默认仍本机 Host；跨网部署属另一专题，不在本期展开。

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| macOS 摄像头权限 / 名称格式 | 实现时真机验证；失败给出权限引导文案 |
| 进程组杀不尽孙进程 | 看门狗 + 运维强制重启；日志记录残留 PID |
| 二进制体积 / 许可 | 不提交二进制；README 下载与校验 |
| Apple Silicon vs Intel | `bin/darwin-arm64` 与 `darwin-x64` 或通用说明二选一（实施时定一种） |

## 实施顺序（确认后执行）

1. 抽出 `platform/`，posix ProcessScope + 文件锁；Mac 可启动控制台并分析 mp4/RTSP。  
2. binaries 解析 + MediaMTX/ffmpeg darwin 引导脚本。  
3. camera avfoundation 枚举与推流。  
4. Windows 回归测试；补 macOS 冒烟说明。  
5. （二期）Linux v4l2 + CI 矩阵。

## 待主人确认

- 本设计是否按 **macOS 一期优先** 实施？  
- darwin 二进制：**不入库、脚本下载**（推荐）还是 **提交精简版到仓库**？
