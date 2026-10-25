# MediaMTX / FFmpeg 二进制

| 目录 | 平台 |
|------|------|
| （根下 `mediamtx.exe`、`ffmpeg_bin/`） | Windows（历史布局，仍支持） |
| `darwin/` | macOS（arm64 / x64 通用说明见下） |
| `darwin-arm64/`、`darwin-x64/` | 可选分架构放置 |
| `linux/` | 二期 |

## macOS 获取

在仓库根执行：

```bash
bash kstool/mediamtx/scripts/bootstrap_darwin.sh
```

或手动将 `mediamtx`、`ffmpeg` 可执行文件放入 `bin/darwin/` 并 `chmod +x`。

Apple Silicon 与 Intel 可分别放入 `darwin-arm64` / `darwin-x64`；若只有一份，放 `darwin/` 亦可（解析时优先架构目录）。
