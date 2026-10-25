# 跨平台（优先 macOS）实施计划

> 规格：`docs/superpowers/specs/2026-10-25-cross-platform-mac-first-design.md`  
> 主人确认：实施；darwin 二进制放进仓库

## Task 1: platform 进程与锁 ✅
- `hostplat/process_posix.py` + `process_win.py`；`process_scope` 按 OS 分发

## Task 2: binaries 解析 ✅
- `hostplat/binaries.py`；`camera_source` / `stream_control` 已接入

## Task 3: avfoundation 摄像头 ✅
- `hostplat/camera.py`；Windows 仍为 dshow

## Task 4: 入库 darwin 二进制 + 启动脚本 ✅
- `bin/darwin*` + `bootstrap_darwin.*` + `启动分析控制台.command`

## Task 5: Windows 回归 + README ✅
