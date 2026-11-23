# 整星存储收支逐步核算

原四效应 Run 在 10–20 s 存储增加 22,500,000 bit，20 s 当时的载荷速率为零。保持同一 TaskSpec、1 s 积分步长和 30 s 时长，将记录间隔由 10 s 改为 1 s。11–19 s 的九步各生成 2,500,000 bit，20 s 的一步为零。逐步累加与两个时刻的存储量之差一致。

新记录的 `data.downlink_node_baud_bps` 来自 DownlinkHandling 发往存储器的带符号节点消息。逐步把它与载荷节点速率相加，与存储器上报的净速率交叉核对。独立核算从首帧存量起步，用每步的节点输入预测下一帧存量，同时核对底层模型的整位取整、容量拒收和空库存边界。SimpleStorageUnit 要求正写入后的总量严格小于容量，超过或恰好达到容量都会拒收该次写入。见[官方 2.11.0 实现](https://github.com/AVSLab/basilisk/blob/v2.11.0/src/simulation/onboardDataHandling/storageUnit/simpleStorageUnit.cpp)。

| 工况 | 每步 / s | 生成 / bit | 因容量拒收 / bit | 从库存扣除 / bit | 最大绝对残差 / bit |
| --- | ---: | ---: | ---: | ---: | ---: |
| [原场景逐步记录](full-rate.csv) | 1 | 47,500,000 | 0 | 0 | 0 |
| [存储容量改为 11,000,000 bit](limited-storage.csv) | 1 | 47,500,000 | 37,500,000 | 0 | 0 |
| [独立标称 RF 工况](active-link.csv) | 0.1 | 7,500,000 | 0 | 4,350,000 | 0 |

三例均使用官方 Windows Basilisk 2.11.0，各有 30 个积分区间，Run Bundle 的验证和完整性检查均通过。10 秒端点采样无法恢复每个动力学步的输入，审计脚本对采样不连续或间隔不符报错。这里核对存储器收支；地面收包准确性和真实设备容量策略需要各自的验证。

复现使用工作树的 src 和根目录作为 PYTHONPATH，按原生整星测试配置 SAT_SIM_ASSET_ROOT。将 `tests/fixtures/celestrak_space_weather_2026-03-04.csv` 复制为 SW-Last5Years.csv，运行 `python scripts/fetch_space_weather_asset.py --offline --output <CSV路径> --source-url test-fixture://celestrak-compatible-sample`，并将 SAT_SIM_SPACE_WEATHER_PATH 指向该文件。在本目录运行 `python run_cases.py task.json <新输出目录>`，随后对三个包分别运行 `python audit.py <包目录> <新CSV文件>`。
