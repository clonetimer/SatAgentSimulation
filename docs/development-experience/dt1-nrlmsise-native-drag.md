# DT1-A1 NRLMSISE-00 整星阻力接入：开发经验与验收边界

日期：2026-11-20
关联：PR #206；计划调整 CHG-DT0-010、CHG-DT0-011、CHG-DT0-012
状态：PR #206 已通过原生阻力 A/B 和 CI，Ready for Review，尚未合并 main。

## 做了什么

在已有 whole_spacecraft.unified_native.v1 的 spice_wmm 轨道环境之上，按显式 opt-in 加入 Basilisk 2.11.0 官方 SpaceWeatherData、MsisAtmosphere、ZeroWindModel、DragDynamicEffector，避免并行重写大气公式。建立本地 CelesTrak 格式 CSV 及 SHA-256/有效日期/来源检查；将 23 路空间天气、密度、温度、本体系力、相对速度和耗散功率送入同一 Run 的证据。R0 不启用大气阻力，默认行为保留。

## 踩坑与改正

1. 首轮 native CI：密度非零、阻力模长为零。发现读取了 dragDynamicEffector 不会更新的继承字段 forceExternal_N，而官方 cannonballDrag() 写的是 forceExternal_B。改用后者计算范数、记录实际本体系力，不凭空转为惯性 XYZ。
2. “力非零”仍不足以证明物理效果。加入 F_B·v_B<=0、相对速度非零和 600 s 的有/无阻力同初始条件轨道半长轴 A/B；在真实 CI #44755915226 上同时通过。
3. CelesTrak 格式兼容不等于真实 CelesTrak 数据。新环境资产清单按真实下载、显式声明、未知来源分别记录；合成夹具明确使用 test-fixture:// 标识。Manifest 的 SHA-256 仅证明本地文件绑定，不是上游数字签名。
4. 原生参数增加后注册表从 169 变成 172，必须更新参数表一致性测试而不能跳过治理；脚本需补用途/参数/输出中文头及 SCRIPT_INVENTORY 记录。
5. 上游 SpaceWeatherData 在缺日期时可能沿用旧值，所以运行前检查 epoch 前三日到结束日逐日覆盖。缺数据、错误磁场档位或 manifest 指纹不符必须失败关闭。

## 当前物理与产品边界

- 真实整星路径为官方 Basilisk 模块与项目模块组成的统一 Process/Task；固定面积和 Cd 是工程假设，不是 CAD 真实投影面积。
- ZeroWindModel 使用地球共转速度，不含热层风场。
- 内部 A/B 证明动力学效应，不等于 STK/GMAT 独立比对，也不等于任务级精度或硬件/飞行标定。
- PR #206 的同头原生 CI、治理和回归已通过，但未经维护者批准不自动合并；若后续失败，仍需定位真实根因，不修改验收门槛。

## 收口验收证据（方案 a）

- 验证 HEAD：`ffecb139f1f9861a80e1ba6ff7d091eff66f7a30`；GitHub Actions [Continuous satellite native Basilisk tests #44755915226](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44755915226) SUCCESS；job `native-whole-spacecraft` 的 `Verify native SPICE WMM, NRLMSISE-00 drag and orbit-step convergence` SUCCESS；环境测试 12/12 PASS。同头 15 条工作流 SUCCESS、1 条 SKIPPED、0 FAIL。
- 原生样本共 61 个：密度 `6.100284068e-11`～`6.256561193e-11 kg/m³`；本体系最大阻力模长 `3.725094387e-02 N`；最小耗散功率 `-2.740595737e+02 W`；最大大气相对速度 `7749.833225185 m/s`。
- 同初始状态 600 s A/B：无阻力末端半长轴 `6647253.359927486 m`，有阻力 `6643441.055106110 m`，差 `-3812.304821376 m`。专用测试条件：质量 `10 kg`、固定迎风面积 `10 m²`、`Cd=2.2`，空间天气是合成 CelesTrak 兼容夹具，不代表默认参数、真实下载或飞行数据。
- 代码证据：`src/sat_sim/bsk_engine/unified_native.py:979-1001` 从官方模块实际赋值的 `forceExternal_B` 读取本体系力；`tests/test_native_orbital_environment.py:165-200` 检验阻力方向、耗散与轨道 A/B，并打印上述真实 CI 数值。

## 开发经验与交付边界

- 首先核查 Basilisk 原生模块实际写入哪个字段及其坐标系；未赋值的 `forceExternal_N` 不等于零阻力。
- 环境输出不等于受力；力非零也不足以证明动力学耦合，应联合耗散功率及同初值轨道 A/B 核验。
- 校验资产完整性的 SHA-256 不等于来源证明；缺少真实发布者信息时不得默认归属 CelesTrak。
- 固定标量面积/Cd 和只含地球共转的 ZeroWindModel 不证明姿态相关气动、热层风、STK/GMAT 独立交叉验证或飞行级精度。
- PR #206 已转 Ready for Review、尚未合并。DT-4.1 档 1 参考外形是独立的 PR #207，未纳入 DT-1 集成分支 #213；档 2 实装映射无安装审批，不得晋升为已验证的安装几何。

## DT-1 四效应集成后的同头复核（PR #213）

- 集成候选 HEAD：`c385d9067109afe9d0b49d90e6e5cabff2030495`；[官方原生整星 CI #44810938798](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44810938798) SUCCESS，同头工作流合计 15 SUCCESS、1 条件性 SKIPPED、0 FAIL。该 HEAD 已将 NRLMSISE 阻力、SPICE/Eclipse SRP、完整 GGM03S 2/3/4 阶、IGRF14/PPIGRF 组合验证，并增加产品 TaskSpec → 真正导出脚本 → Run Bundle 的执行门禁。
- Git Blob 核对显示，#206 的 `tests/test_native_orbital_environment.py`、`tests/test_space_weather_asset_validation.py`、`scripts/fetch_space_weather_asset.py` 与 #213 对应内容完全一致；本文补回 #206 后期开发经验，并不改变物理实现。
- #213 保持 Ready for Review、尚未合入 main。此处报告的 CI 仅对应前述明确 HEAD；补充本文件的新提交需在其自身 HEAD 再验收，不能继承旧 SHA 的绿色状态。
