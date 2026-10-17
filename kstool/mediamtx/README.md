# 矿区摄像头 RTSP 模拟服务（MediaMTX）

> 运维、备份与回滚见 [../../docs/operations.md](../../docs/operations.md)；
> 联合验收见 [../../docs/acceptance.md](../../docs/acceptance.md)。

## 目录结构
- `bin/mediamtx.exe` — 流媒体服务器（v1.21.0）
- `bin/ffmpeg_bin/` — FFmpeg（负责把本地视频循环推成 RTSP 流，或采集本机摄像头）
- `mediamtx.yml` — 完整配置（18 路）；`mediamtx-lite.yml` — 精简配置（8 路）
- `mediamtx-live.yml` — 本机摄像头模式配置（由控制台“本机摄像头接入”面板生成受管段）
- `stream_control.py` — 安全启停入口：MediaMTX 与 FFmpeg 归入项目专属作业对象

## 本机摄像头接入（live 模式）
控制台“视频”页顶部面板可枚举本机摄像头并写入 `mediamtx-live.yml`（受管段，可重复增删，
不覆盖手写 path）。启动：

```powershell
kstool\启动-本机摄像头.bat
# 等价：python kstool\mediamtx\stream_control.py start --mode live
```

面板会给出 `rtsp://127.0.0.1:8554/livecamN`，填入“添加视频”即可进入现有分析链路。
停止用 `kstool\停止.bat`（与模拟流共用 session，同一时刻只允许一组流服务）。

## 启动（推荐：一键脚本）
解压到**任意位置**后，双击对应的 bat 即可，不用改任何路径。

| 脚本 | 推流路数 | 适用场景 |
|---|---|---|
| `start_streams.bat` | 18 路（camera1~18） | 完整演示、答辩 |
| `start_streams_lite.bat` | 8 路（camera1~8） | 低配电脑、只要主要点位、降低 CPU 占用 |

脚本内部统一调用 `stream_control.py`：环境自检 → 端口冲突检查 → 以作业对象
启动 MediaMTX → 拉起 FFmpeg 推流 → 就绪后打印 RTSP 地址，并自动打开浏览器
预览 camera1。启动后没有需要保持打开的控制台窗口；关闭启动窗口不会停止流服务。

停止：双击 `stop_streams.bat`。停止命令只操作本项目拥有的作业对象，不会按进程名
或窗口标题结束其他 MediaMTX/FFmpeg；身份校验失败时拒绝强制终止。

> 首次运行时若弹出 Windows 防火墙，请点"允许访问"，否则外部设备拉不到流。
> 端口被其他程序占用时会直接报冲突并拒绝启动，不会尝试释放对方端口；
> 可用 `--config` 指定自定义端口的配置文件：
> `启动-完整18路.bat --config D:\my-mediamtx.yml`

## 启动（手动方式，不推荐）
直接运行 `bin\mediamtx.exe mediamtx.yml` 会绕过作业管理：停止脚本、崩溃回收和
单实例保护都不再生效。需要排障时可临时使用，正常请走上面的 bat 或：
```
python stream_control.py start --mode full
```

## RTSP 地址（前端直接填）
| 点位 | 地址 |
|---|---|
| 综采工作面（正常） | rtsp://127.0.0.1:8554/camera1 |
| 掘进工作面（正常） | rtsp://127.0.0.1:8554/camera2 |
| 顺槽巷道（正常）   | rtsp://127.0.0.1:8554/camera3 |
| 井下中央变电所     | rtsp://127.0.0.1:8554/camera4 |
| 机电硐室           | rtsp://127.0.0.1:8554/camera5 |
| 修理车间           | rtsp://127.0.0.1:8554/camera6 |
| 掘进工作面（风险） | rtsp://127.0.0.1:8554/camera7 |
| 综采工作面（风险） | rtsp://127.0.0.1:8554/camera8 |

局域网其他设备访问时，把 `127.0.0.1` 换成本机 IP（`ipconfig` 查看）。

## 快速预览
- 播放器（VLC/PotPlayer）：打开任意 `rtsp://127.0.0.1:8554/cameraX`
- 浏览器：`http://127.0.0.1:8888/camera1`（HLS 预览）
- 在线流列表：`http://127.0.0.1:9997/v3/paths/list`

## 更换/增加点位
编辑 `mediamtx.yml` 中对应 `cameraN` 的视频路径，重启服务即可。
视频要求 H.264 编码（当前所有矿山视频均满足，`-c copy` 不转码）。

## 接入真实摄像头（落地阶段）
把某一路的 runOnInit 段替换为静态源：
```yaml
camera1:
  source: rtsp://10.x.x.x:554/Streaming/Channels/101
```
前端代码零改动。

## 答辩话术
"当前演示阶段使用本地视频模拟矿区摄像头 RTSP 流；系统原生支持接入真实矿山内网
摄像头（rtsp://10.x.x.x），只需替换 RTSP 地址即可落地，接入逻辑与工业摄像头完全一致。"
