# 原生实时长尾诊断

`scripts/diagnose_native_timing.py`用于在独立进程中复制应用源码、运行原有真实Basilisk持续仿真入口，采集完整短测计时。它不会连接、暂停、重启或修改已有耐久实例。复制前后应用文件逐字节核对；轨道环境仍显式使用本机已有SPICE/WMM/J2资源并校验哈希，不回退为简化环境。

```powershell
.venv\Scripts\python.exe scripts/diagnose_native_timing.py --output-dir reports/local_requirements_completion/my-native-timing --wall-seconds 1810
```

输出目录必须是本仓库`reports/local_requirements_completion`下的新目录，不能复用已有运行。主环境已经由uv安装并含真实原生依赖。

工具只降低自己的优先级，并在存在时选择允许范围内编号12及以上的逻辑CPU；新原生子进程继承允许范围。这不保证物理资源独占，也不代表生产部署负载。运行中应记录同时发生的其他工作；不可把其结果直接作为正式生产性能资格。

采集内容包括物理进程RPC/推进、遥测发布、持久化入口、编码、写入、flush、实际fsync、工作区锁等待/持有、分片轮转，以及原始`PaceObservation`的单调时钟与漂移。`clock.py:pace_to`的跨度包含正常的主动等待，不能当作计算耗时；Python跨度为包含子调用的时间，不可直接相加。原生子进程内部物理计算与IPC耗时目前未进一步分离。

诊断器只在本进程临时包装fsync及工作区锁，原始fsync仍实际执行，命令执行栅栏/ACK不省略。结束时恢复包装。所有完整计时数据先驻留内存，最多100万条；超限明确报告不完整，不能静默当成完整证据。进程崩溃可能丢失尚未输出的诊断样本；此工具不是新的遥测持久化机制。

正常结束输出：

- `diagnostic.json`：源码、资源、进程身份、采样统计和失败响应。
- `timing.jsonl`：单调时钟跨度与线程标识。
- `pacing.jsonl`：完整原始漂移记录，可超过会话4096条滑动保留范围。
- `native-run/`：原入口的真实遥测、任务判定与封存证据。
- `candidate/`：字节一致的源码/配置副本；`diagnostic_source.py`保存本次实际工具源码。

至少覆盖完整1800秒时才计算完整30分钟窗口；不足时`complete_30min_windows`为空。即便有完整窗口，剖析开销和诊断资源配置仍须披露。原入口的短测状态为`SMOKE_COMPLETE_NOT_24H`，耐久审计应为`NOT_QUALIFIED`，不能改成24小时通过。

仅验证采集器归因能力时可添加`--inject-fsync-delay-s 2`。这会在新诊断实例的首次遥测fsync前人为等待，不模拟物理故障；报告将其与实际fsync计时分别保存。该试验不能证明真实长尾由磁盘造成。

2026-10-30的前身诊断器已完成15秒注入与60秒基线真实原生运行，44项独立核对通过，共同32个原生采样点的全部值完全一致。60秒基线漂移P95约0.904 ms、最大约2.59 ms；注入等待约2001.77 ms，而该次实际遥测fsync最大约5.36 ms。两个试验均封存，未取得24小时资格。原始启动失败、CLI零退出被包装器误判失败及首版复核的浮点时间比较错误均保留，最终复核按原生整数纳秒网格检查，未改遥测。

公共入口随后已实际执行10秒原生短测并封存，21项独立制品/计时检查通过，全部21个漂移样本保存，源码/共享资源哈希前后相同。锁异常释放与完整窗口样本数/单位/短窗口拒绝也已单独检查。以上不冒充本公共入口已经执行过完整30分钟；约30分钟的前身采集仍在进行。

资料与下一步设计见[剩余工作与公开资料方案](../reports/remaining_work_and_public_research.md)。当前尚未实施异步遥测、组提交或生产时序修复。

## 读取完成后的计时记录

```powershell
.venv\Scripts\python.exe scripts/analyze_native_timing.py reports/local_requirements_completion/my-native-timing --output reports/local_requirements_completion/my-native-timing-analysis.json
```

分析器要求实际运行已结束、计时文件哈希和样本数相符，绑定每个推进步骤及漂移观测，再定位超出200 ms的漂移片段。200 ms仅用于定位片段，不是新增的最大漂移验收阈值。结果区分主动睡眠超时、推进调用区间、工作区锁等待及未测量区间；嵌套跨度按时间并集计算，避免重复相加。磁盘调用的墙钟跨度仍包含系统调度，不能单独作为磁盘设备故障的根因证明。

实际注入/基线数据和故意损坏的副本已完成13项检查：识别已知2001.77 ms停顿、排除启动RPC、拒绝哈希篡改/样本不符/运行未结束，并验证时间并集。原24小时实例不由此工具访问或修改。短测原入口的耐久采样周期为1秒，实际24小时实例为5秒；诊断剖析开销、CPU范围及同时发生的其他负载均需披露。

[微软FlushFileBuffers说明](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers)讨论逐次同步写入的开销；这为评估写入策略提供依据，不证明本次延迟由存储介质导致，也不支持直接关闭持久化。一次猜测的WPR文档深链未能访问，未作为已验证的操作流程。
