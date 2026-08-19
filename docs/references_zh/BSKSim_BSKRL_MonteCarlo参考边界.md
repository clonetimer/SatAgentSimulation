# Basilisk、BSKSim、BSK-RL 与 Monte Carlo 参考边界

## 1. Basilisk

项目以 Basilisk 2.11.0 作为核心航天器仿真依赖，并通过已注册的 builder、adapter、runner 和项目自有耦合模块调用。是否使用 Basilisk 原生模块，应以具体能力合同、执行计划和运行证据为准，不能由“项目依赖 Basilisk”推导为所有模型均为 Basilisk 原生实现。

## 2. BSKSim-style

本项目的 `sat_sim.bsk_engine` 借鉴场景、Dynamics、FSW、Process/Task、事件和记录器分层思想，形成项目自有 BSKSim-style 合同。当前包括：

- 基础轨道姿态场景；
- ADCS 迁移桥；
- 整星强耦合桥。

这些能力不等同于已直接引入完整官方 BSKSim 工程模板。

## 3. BSK-RL-style

项目只参考以下抽象：

- 故障状态；
- 触发和活动窗口；
- before/during/after 观测；
- episode 级证据；
- 故障效果合同。

未引入：

- Gymnasium 主接口；
- reward；
- policy；
- RL 训练；
- Ray/RLlib 依赖。

因此正式表述必须为“BSK-RL-style 故障环境适配”或“参考 BSK-RL 思想”。

## 4. Monte Carlo

实验中心支持：

- 枚举扫描；
- 网格扫描；
- 固定种子随机采样；
- dispersion registry；
- 单次 Run Bundle 保留；
- mean/std/min/max/p5/p50/p95/pass_rate 等统计摘要。

当前执行链为：

```text
TaskSpec + SamplingPlan
→ 变体 TaskSpec
→ 独立 Run Bundle
→ 统计汇总
```

尚未直接创建和运行 Basilisk 官方 `MonteCarloController` 对象，因此不得表述为“基于官方 Controller 执行”。
