# 本机摄像头多路选择实施计划

目标：自动发现设备，每路选择一个模型，一次完成保存和启动。

架构：camera_source 负责设备与配置；live_cameras 负责批量编排；Controller 保留已有启动与容量守卫；独立 live-cameras.js 负责面板交互，复用控制台样式。

技术栈：Python 标准库、DirectShow／FFmpeg／MediaMTX、原生 JavaScript。

在本会话按测试先行顺序执行，不派发子代理。

1. [ ] 设备及配置：在 tests/test_camera_source.py 添加同名设备唯一标识、默认帧率、手写重名和损坏标记测试，先观察失败；修改 camera_source.py 使其通过。序列化写入，原子替换，统一端口解析。
2. [ ] 批量编排：新增 tests/test_live_selection.py，使用临时配置与模拟硬件边界，验证重复保存、单模型约束、离线恢复、部分失败和容量。新增 live_cameras.py，接入 console.py。停止脚本增加目标模式守卫并测试。
3. [ ] 界面：新增 live-cameras.js，原面板替换为设备选择表，保留已选草稿、自动恢复配置，独立显示扫描、保存、取流和分析状态；修改 console.html/css/js 与静态路由。
4. [ ] 回归：执行相关测试及无真实设备副作用的回归；JavaScript 语法与界面交互验证；更新 README 使用方式。清理临时脚本，检查 diff 和工作区，只报告实际执行的验证。

验证命令通过根目录临时 _run_ 脚本调用 analysis-service/.venv/Scripts/python.exe -m unittest discover；前端使用 node --check 及模拟接口交互验证。
