# 矿区摄像头 RTSP 模拟服务（MediaMTX）

## 目录结构
- `bin/mediamtx.exe` — 流媒体服务器（v1.21.0）
- `bin/ffmpeg_bin/` — FFmpeg（负责把本地视频循环推成 RTSP 流）
- `mediamtx.yml` — 核心配置：camera1~camera8 共 8 路模拟摄像头

## 启动（推荐：一键脚本）
解压到**任意位置**后，双击对应的 bat 即可，不用改任何路径。

| 脚本 | 推流路数 | 适用场景 |
|---|---|---|
| `start_streams.bat` | 18 路（camera1~18） | 完整演示、答辩 |
| `start_streams_lite.bat` | 8 路（camera1~8） | 低配电脑、只要主要点位、降低 CPU 占用 |

脚本会自动完成：环境自检 → 端口检查 → 启动 MediaMTX → 拉起 FFmpeg 推流
→ 就绪后打印本机/局域网 RTSP 地址，并自动打开浏览器预览 camera1。

停止：双击 `stop_streams.bat`（会同时停掉 MediaMTX 和全部 FFmpeg）。

> 首次运行时若弹出 Windows 防火墙，请点"允许访问"，否则外部设备拉不到流。
> 不想自动弹浏览器：把 bat 里的 `AUTO_OPEN` 改成 `0`（或运行前 `set AUTO_OPEN=0`）。

## 启动（手动方式）
在本目录打开终端执行：
```
bin\mediamtx.exe mediamtx.yml
```
看到 `startup complete` 即启动成功，18 路流自动上线。

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
