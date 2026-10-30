# 通用近地小卫星工程参考场景

使用目的：比较声明的工程参数、环境假设和故障对轨道、姿态、能源及热状态的影响。参数不是某颗在轨卫星的标定值，结论只覆盖实际验证的配置和工况。4 W/K 热连接配置及配套表单已合入生产目录，三组模拟日独立复核通过；生产路径复验和实际耐久尚未完成。最新判定以[当前验收状态](../reports/current_requirements_status.md)为准。

## 参考输入及其含义

| 项目 | 值 | 来源／解释 |
|---|---|---|
| 环境配置 | `spice_wmm` | 使用本地实际 SPICE／磁场资产；资产身份见原生运行摘要 |
| 初始历元 | `2026-01-01T00:00:00Z` | 可复现的场景选择，不表示实时观测历元 |
| 初始轨道半长轴 | 7000000 m | `unified_native.py` 默认经典根数；字段名为 `orbit_radius_m`，在初始化中作为半长轴 `a` 使用 |
| 偏心率／倾角 | 0.001／35° | 同一原生初始化的默认工程假设 |
| 升交点赤经／近地点幅角／初始真近点角 | 0°／0°／0 rad | 同一原生初始化的默认值 |
| 星体质量 | 100 kg | 未提供覆盖值时的工程假设 |
| 星体主惯量 | 100、80、60 kg·m² | 对角刚体惯量；质量与惯量为独立输入，不自动按质量缩放 |
| 太阳翼—结构等效导热系数 | 候选 4 W/K；兼容默认 2 W/K | 替换同一条原生热连接的系数；4 W/K 是假设的设计改进，不是硬件测量 |
| 地球红外／反照率 | 标称 234 W/m²／0.30 | 均匀全球均值假设；另外测试 227／0.25 和 241／0.35 |
| 积分步长／遥测间隔 | 0.1 s／0.5 s | 参考连续原生运行；步长细化对照使用 0.05 s |

省略的参数继续使用被验证源码中的默认值，不能解释为测得的整星配置。审查某次具体运行时，应共同保存输入文件、规范化配置、实际 TaskSpec、源码清单、环境资产摘要、原始遥测和结果，避免只保留这张表。

该场景的初始 TaskSpec 中 `duration_s=60` 表示连续运行的初始化配置。连续模式会推进至显式停止；有限模式的时长字段不构成连续会话的截止时间。模拟日测试实际推进至少 86400 仿真秒，实际耐久试验另按墙钟计时，两者不累计。

## 热配置的证据与限制

原默认 2 W/K 配置在同一参考场景中，太阳翼第 7294 仿真秒首次低于 200 K，全天最低约 197.49547 K。0.1／0.05 秒步长均复现越界；原连续运行的对应轨段与有限运行吻合。原始失败保留，温度下限没有调整。

显式 4 W/K 配置的标称模拟日已独立复核 172806 帧原始遥测，太阳翼最低 203.56923 K；较低全球均值模拟日最低 202.44785 K，较高全球均值模拟日最低 204.66411 K。三组八节点均未触发各自声明温限，各自推进至少 86400 仿真秒并完成封存。标称 8400 秒轨段的两步长最大太阳翼采样温差约 0.005073 K；这只表示局部离散化比较，不能作为整天、全部工况或真实硬件的严格误差上界。

结果只覆盖每 0.5 秒发布的样本，不证明采样间不存在峰值。均匀全球均值比较不包含局地瞬时冷热极限、云层变化、CAD 自遮挡、MLI 细节、制造误差或在轨标定；也不是概率不确定性分析。

独立证据位于 `reports/local_requirements_completion/`：

- `graph-containment-v8-day-forecast-thermal-independent-review.json`：原配置全天失败。
- `graph-containment-v8-first-eclipse-independent-review.json`：原配置两步长复核。
- `thermal-link-v9-environment-campaign/nominal-independent-review.json`：候选标称全天。
- `thermal-link-v9-environment-campaign/lower-global-mean-independent-review.json`：候选较低全球均值全天。
- `thermal-link-v9-environment-campaign/higher-global-mean-independent-review.json`：较高全球均值全天。
- `thermal-link-v9-step-refinement-independent-review.json`：候选两步长和连续／有限轨段比较。

## 运行与审查

候选输入为 `configs/interactive/generic_leo_thermal_link_candidate.json`。在选定源码及该配置合入、生产复验通过后，可使用已有 uv 环境运行实际耐久：

```powershell
uv --cache-dir .uv-cache run --no-sync python scripts/run_native_endurance.py --initial-configuration configs/interactive/generic_leo_thermal_link_candidate.json --output-dir reports/generic_leo_reference_endurance --wall-seconds 86430
```

使用全新的输出目录保存证据。运行器会在创建原生实例前严格校验配置，保存并哈希原始输入与规范化配置，核对实际 TaskSpec，并记录源码／运行器身份。短测始终标记为短测，不授予 24 小时资格。

实时模式提供软实时推进和漂移观测；历史最大漂移会保留。耐久通过、任务有效性和全程时序表现需要分别审查。异常中止后保留原场景证据并创建新场景，不支持同一原生物理状态的跨进程续算，也不自动重放历史指令。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=traceable_reference_configuration; phase=selected_source_installed_production_qualification; evidence=original_failure_three_G4_days_independent_PASS; completion=unproven -->
