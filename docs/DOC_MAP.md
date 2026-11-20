# 文档地图

第一次跑仿真：先看 **README 的三条启动命令**，再看 **工作台使用指南**。要判断结果能说明什么，直接跳到“验收证据”；无需先读开发计划。

## 入门

| 入口文件 | 什么时候看 |
|---|---|
| [README.md](../README.md) | 项目用途、当前安装入口、第一轮仿真和明确限制。 |
| [START_HERE.md](../START_HERE.md) | 第一次选对象、运行、看曲线、复用配置；其中旧 README 章节编号按当前 README 标题查找。 |
| [工作台使用指南](guides_zh/02_工作台使用指南.md) | 普通向导、三种运行方式、参数与输出；先读第 1 节和第 13～16 节。 |
| [故障排查指南](guides_zh/09_故障排查指南.md) | 安装、启动、运行队列或模型服务有问题时查。 |

## 使用

| 入口文件 | 什么时候看 |
|---|---|
| [软实时交互仿真指南](guides_zh/11_软实时交互仿真指南.md) | 服务端开放实时能力后，使用会话控制、遥控和遥测。 |
| [持续卫星仿真指南](guides_zh/13_持续卫星仿真指南.md) | 持续推进、停止归档，以及不能跨进程续算的边界。 |
| [目标实验工作台使用指南](guides_zh/12_目标实验工作台使用指南.md) | 在参数白名单和预算内做多轮目标实验。 |
| [图形组装教程目录](tutorials_zh/README.md) | 需要拖模块、装配、观察器、调参或导出程序包时查；不是首次启动前置阅读。 |
| [模型下载与部署](model_distribution.md) | 需要可选的自然语言辅助时，准备模型权重与推理服务。 |
| [单机参量命名规范](guides_zh/P9A_单机参量命名规范.md) | 理解对象、参量的中文名称、技术字段和单位。 |

## 开发

| 入口文件 | 什么时候看 |
|---|---|
| [开发者指南](guides_zh/06_开发者指南.md) | 理解后端执行链、API 与扩展位置。 |
| [TaskSpec 规范](TaskSpec规范.md) | 编写或审查配置、参数、事件和输出合同。 |
| [Agent 开发者指南](Agent开发者指南.md) | 修改自然语言解析、工具或实验协调行为。 |
| [单机能力与原生范围清单](engineering/p9-n-component-capability-inventory.md) | 查部件的独立入口、物理简化和当前支持范围，按最新收尾节阅读。 |
| [脚本入口说明](../scripts/README.md) | 找维护、诊断、构建与验收脚本，避免猜测启动命令。 |

## 高可信仿真升级（DT-0）

| 入口文件 | 什么时候看 |
|---|---|
| [DT-0 需求覆盖矩阵](engineering/dt0-requirements-coverage-matrix.md) | 逐条核对后端仿真要求与前端交互要求的现状、证据和缺口。 |
| [DT-0 物理模型能力审计](engineering/dt0-physics-model-capability-audit.md) | 区分推荐整星路径、实验性 Basilisk 扩展、项目模型和未验证物理能力。 |
| [DT-0 LEO-Engineering 基线](engineering/dt0-leo-engineering-baseline.md) | 查看首期 LEO 的现状 R0、目标 R1、验证用例和误差预算原则。 |
| [DT-0 计划调整记录](engineering/dt0-plan-adjustment-log.md) | 查看每次路线调整的触发证据、必要性、充分性和验收条件。 |

## 验收证据

这里区分能力声明、测试定义和已保存的运行结果；测试文件存在不等于该次测试已执行，历史通过也不自动覆盖新版本。

| 入口文件 | 能核对什么 |
|---|---|
| [整星资源范围清单](whole-spacecraft-power-authority.md) | 各整星模型的功率来源、保留的工程模型，以及 Unified / Composite 的不同证据范围。 |
| [P10-A7 运行证据](../reports/p10a7-resource-closure-evidence.json) | 公开配置的实际输入、来源指纹、功率清单、电池与入热测量。 |
| [网页创建与历史场景回放测试](../tests/test_p9l4_offline_native_ui_browser.py) | 新人从真实网页创建整星离线任务，原生运行、封存并回放同一任务的验收合同。 |
| [真实遥测驱动场景测试](../tests/test_p9f_native_browser.py) | 限时、持续会话与历史运行的真实轨姿来源和渲染核对。 |
| [整星事件目录测试](../tests/test_p9o3_whole_spacecraft_guided_event_catalog.py) | 当前整星向导开放哪些故障、退化、约束，哪些仍未开放。 |
| [发布声明](../src/sat_sim/release_manifest.json) | 整体发布资格、标定等级及尚未重新获得资格的范围。 |
| [标定数据说明](../calibration/README.md) | 真实测量数据的缺口与接入要求；不把工程假设当作标定。 |
| [验收历史](../reports/ACCEPTANCE_LOG.md) | 原 README 的历史结论、指标、指纹和完整原文；按原日期与版本追溯。 |

## 已归档

下面的历史快照此前已存于各自 `history/` 目录；新的统一归档区尚未迁入文件，归档候选与实际迁移记录分开列。

| 入口文件 | 这是什么 |
|---|---|
| [归档索引与候选清单](ARCHIVE_INDEX.md) | 待审的“原路径 → 拟归档路径 → 理由”，以及暂缓和保留项；本轮没有执行目录迁移。 |
| [R46 之前的 README 快照](../reports/progress_checkpoint/r46-interaction-ownership/history/root-README.pre-current-r46.md) | 当时的项目首页，供追溯旧结论。 |
| [R46 之前的需求状态快照](../reports/progress_checkpoint/r46-interaction-ownership/history/current_requirements_status.pre-current-r46.md) | 当时的验收状态，不能替代当前模型与发布声明。 |
| [R45 之前的进度快照](../reports/progress_checkpoint/r43-integration/history/progress_checkpoint_README.pre-r45.md) | 早期进度记录，供复核阶段变化。 |

其余 R84/P9 预注册、单次实验和已收口检查点见[归档区清单](ARCHIVE_INDEX.md)，拟归入 `docs/archive/2026-11/`，审阅前仍保留原位。

早期 [P9-C 场景说明](guides_zh/P9C_卫星数字场景本地运行.md)中的“仅演示、后续接真实仿真”是当时的阶段描述；当前功能应结合上面的网页指南、真实运行与场景测试阅读，不能用它否定已接入的真实遥测与封存回放。
