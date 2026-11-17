# P9-N1：反作用轮单机仿真工作台

日期：2026-11-17

## 目标

让用户不必了解 Capability、TaskSpec 或 backend 名称，也能在快速仿真向导中选择反作用轮，直接得到官方 Basilisk 原生单机实验配置，检查参数并执行一次有限时长仿真。

## 用户流程

1. 快速仿真首页选择「单机独立仿真」。
2. 首批单机列表仅显示已经完成官方 Basilisk 原生独立物理验证的反作用轮。
3. 选择反作用轮后，系统通过现有场景模板 API 加载 `component_reaction_wheel_basilisk_native`。
4. 将 TaskSpec 无损投影到现有 Schema 表单；backend 默认是 `basilisk_native`，轮数为 1。
5. 用户检查运行时长、采样间隔和物理参数；运行模式仅显示有限时长离线仿真。
6. 执行既有 TaskSpec → Run Bundle → registered capability adapter → Basilisk → sealed result 路径。
7. 查看真实轮速、实际施加电机力矩和摩擦力矩曲线及 Run Bundle 来源证据。

## 关键边界

- 不新建 component runtime 或第二套表单。
- 不默认切换项目参考模型为 Basilisk；只有专用单机入口和模板明确设置 native backend。
- 当前 Basilisk RW 原生模型仅 nominal。此向导不提供原生故障、退化、约束或实时连续模式。
- 3/4 轮构型属于多轮组件组合，不在首例单个反作用轮流程中自动选择。
- 其他单机不出现在「已验证原生单机」入口，直到各自拥有独立模型图和端到端证据。
- 工程 CAD 映射不作为本次单机物理运行的前置条件；CAD 仍是参考几何，尚未绑定为已审定安装实例。

## 验收

- Chromium 用户旅程能够从向导首页进入单机独立仿真。
- 选择反作用轮后自动应用官方 Basilisk 模板。
- 表单显示并保留 `physics_backend=basilisk_native`、`num_wheels=1`、`max_motor_torque_nm=0.005`。
- 实时/持续实时选项不出现在此专用流程。
- 完整 Run Bundle 集成测试 PASS，官方 Basilisk 2.11.0 证据、原生 recorder 输出、扭矩饱和和 SHA-256 封存校验全部保留。
- UI 不将项目参考模型或整星实时输出伪装为单机原生实验。


## 默认结果视图

原生反作用轮模板明确选择两条最重要的物理响应曲线：
- 反作用轮 1 角速度（rad/s）。
- 反作用轮 1 实际电机力矩（N·m）。

并默认选择初始角速度、末时角速度和全程最大绝对角速度三个结果指标。这样用户首次运行即可观察输入力矩如何驱动轮速变化，以及实际施加力矩如何受到 Basilisk 硬件上限约束，而不需要先理解完整遥测字段目录。


## 历史运行复用验收

浏览器端还必须从 Run Center 重新打开本次成功封存的 Run，并确认：
- 运行摘要仍显示 Basilisk 官方原生 backend 和 `ReactionWheelStateEffector` provenance。
- 重新进入结果曲线后，轮速与实际电机力矩两条物理曲线仍可见。
- 历史查看只读取原 sealed Run，不创建第二次运行，也不重放任何控制命令。
