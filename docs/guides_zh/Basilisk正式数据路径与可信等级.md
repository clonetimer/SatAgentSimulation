# Basilisk 正式数据路径与可信等级

## 1. 适用范围

本说明约束卫星仿真 Agent 平台向 AstroGraph 提供训练、验证、Benchmark 和候选知识抽取数据时的仿真路径。结论不适用于单元测试、接口联调或 UI 演示。

## 2. 默认原则

正式数据默认且必须使用：

- 能力：`subsystem.adcs_unified_native.v1`；
- 后端：`basilisk`；
- 运行时：可导入且发行身份为 `bsk==2.11.0` 或经逐文件载荷校验的 metadata-only `bsk==2.11.0+satfix1`；
- 证据：运行摘要必须证明 Basilisk 模块已经实例化、连接并被 Recorder 记录。

`subsystem.adcs_fidelity.v1` 等纯 Python 工程代理只允许用于单元测试、接口测试和 Agent 流程测试。它们生成的数据一律标记为 `C_TEST_PROXY_ONLY`，不得进入正式模型训练、模型绑定、Benchmark 或知识图谱发布流程。

## 3. 可信等级

| 等级 | 判定条件 | 允许用途 | 禁止用途 |
|---|---|---|---|
| A_ENGINEERING_BASILISK_NATIVE | 正式能力、Basilisk 后端、受支持版本、完整运行时实例化证据 | 训练候选、Benchmark 候选、专家审核候选 | 飞行级或在轨等价声明 |
| B_BASILISK_UNVERIFIED_OR_INCOMPLETE | 请求了 Basilisk，但版本或运行证据不完整 | 运行时修复、工程调试 | 正式训练、模型绑定、KG 入库 |
| C_TEST_PROXY_ONLY | 非 Basilisk 能力或后端 | 单元测试、接口测试、Agent 工作流测试 | 正式训练、Benchmark、知识构建 |

等级 A 仍然是工程仿真证据，不等同于飞行相关性或在轨验证。

## 4. 安装闭环

默认引导脚本执行 `scripts/ensure_basilisk_runtime.py`：

1. 验证当前环境是否已安装受支持的 Basilisk；
2. 若未安装，按平台下载固定的官方 2.11.0 Wheel；
3. 校验官方 SHA-256；
4. 仅重写依赖元数据生成 `2.11.0+satfix1`，不修改 Basilisk 二进制和 Python 仿真载荷；
5. 安装并重新验证 `bsk` 发行身份和 `Basilisk` 导入根。

无法访问官方 Wheel 时必须阻断正式数据执行，不能自动回退到 Python 代理。

## 5. AstroGraph 反作用轮 Profile

本阶段发布：

`astrograph-rw-temporal-basilisk-5ch.v1`

必需通道：

1. 目标轮转速；
2. 目标轮等效摩擦力矩；
3. 目标轮实际施加力矩；
4. 姿态指向误差；
5. 目标轮可用状态。

可选上下文通道：

- 电机电流；
- 有效最大力矩。

电机电流在没有受审查的电机电气模型前不得通过经验常数伪造。六通道冻结 Profile 暂不声明兼容。

## 6. 数据门

正式案例只有同时满足下列条件，`training_ready` 才能为真：

- 遥测文件非空；
- 五个必需通道全部可投影；
- 仿真可信等级为 A；
- 运行清单包含 Basilisk 实例化模块证据；
- 数据合同、仿真报告和文件哈希完整。

## 7. 当前状态

当前环境已安装受支持的 Basilisk 运行时。RW 原生信号门已完成 18/18 案例、9/9 物理配对和 9/9 诊断候选配对；具名批准只适用于精确冻结的工程仿真合同、签名哈希和九个 Pair。
