# 联合验收报告（短时，2026-10-17）

## 1. 基本信息

- 执行时间：2026-10-17（Asia/Singapore）
- 命令：`python acceptance.py --minutes 1.5`
- 数据目录：临时目录（验收结束删除，未覆盖 `data/events.db` 与现有 outbox）
- 报告文件：`analysis-service/runs/acceptance-20261017-080254/report.json`、`report.md`
- 环境：Windows、项目 Python 虚拟环境、CPU 推理（torch 2.6.0+cpu）

## 2. 配置

| 项 | 值 |
|---|---|
| 视频配置 | 6 路（2 路隔离端口模拟流 + 4 路本地素材） |
| 分析路数 | 4 路（2 路模拟流 + 2 路本地） |
| 任务 | `person`，`interval=1`，`confidence=0.4` |
| 模拟流 | 隔离端口 MediaMTX lite（避免与默认 8554/8888 冲突） |
| 时长 | 1.5 分钟，45 次采样（间隔 2 秒） |

## 3. 测量结果

| 指标 | 结果 |
|---|---|
| 结果年龄 P50 | 0.752 秒 |
| 结果年龄 P95 | **0.919 秒** |
| 结果年龄 Max | 0.976 秒 |
| 生命周期 | 4 路 `active`、2 路 `stopped`（未启动） |
| 模型错误 | 无 |
| 事件错误（worker/model/alert_rejected） | 无 |
| 告警桥 | `pending=0, dead=0, reported=0, failed=0` |
| CPU（进程口径） | 298.7% |
| 内存（分析进程） | 1319.7 MB |
| 磁盘 | `ok`（约 425 GiB 可用） |
| 维护预览 | 候选可枚举、无错误 |

## 4. 观察项

- P95 在 3 秒内：是（短时、本机、CPU 推理）
- 全部分析路为 `active`：是
- 模拟流已接入分析：是

## 5. 限制

1. 短时验收不能代表 8 小时稳定性；长时验收请以 `--minutes 480` 在固定机器执行，清单见 `docs/acceptance.md`；
2. 性能结论只对本机与本组素材成立；P95 超过 3 秒时应降低并发或增大任务间隔并记录配置；
3. 未设置 Java 告警接口，告警桥计数为 0（链路由批次 5 测试覆盖）；
4. 没有现场标注集，未计算准确率（工具与边界见 `evaluate_accuracy.py`）。

## 6. 下一步

1. 在固定机器执行 8 小时稳定性验收并保存报告；
2. 按现场标注集运行 `evaluate_accuracy.py evaluate`，分别统计人员/装备/烟火漏报与误报；
3. 联调 Java `alertCode` 幂等（至少一次投递语义）。
