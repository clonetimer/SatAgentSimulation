# DT-1｜main a5147550 合并后实测验收报告

- **验收目标 SHA**：`a5147550a0290a31705fdf45c094517d20345e0d`（merge commit，main）
- **执行时间**：2026-11-21 11:57–12:05 UTC，GitHub Actions `push/main` 自动触发；本报告逐项核查的是该 SHA 的**实际运行日志和断言**，不是源 PR 成绩或后续报告分支成绩。没有额外发起第二次相同 SHA 的执行。
- **环境**：Ubuntu GitHub-hosted runner、Python 3.13、实际安装的上游 `bsk==2.11.0`（日志：`Basilisk: 2.11.0`）；真实 Playwright Chromium；部分组件在不同 job 上运行。
- **合格定义**：每项三态 `PASS / FAIL / INCONCLUSIVE`。仅 `completed/success` 不是物理实测数字；物理项须有该 SHA 日志中的数值及现存测试的接受判据。
- **本报告交付方式**：新增报告文件的独立 PR；不推 main，不修改生产物理参数/原测试阈值；#206/#208/#210/#211/#212 继续 OPEN。

## 1. exact-commit 运行证据索引

在 GitHub Actions run API 中，以下八个工作流的 `head_sha` **全部**是上述 40 位 SHA，`event=push`、`status=completed`、`conclusion=success`、`run_attempt=1`。工作流成功不得换算为 R1 需求完成率。

| 工作流与运行 | Job / 已执行范围 | 工作流状态 |
|---|---|---|
| [Continuous satellite native Basilisk tests #44901527137](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527137) | [native-whole-spacecraft #136838259990](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527137/job/136838259990)；Windows lifecycle / C++ source contracts 另有成功 jobs | PASS |
| [Independent native component and subsystem physical and Run Bundle acceptance #44901527043](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527043) | [official-basilisk-native-components #136838259631](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527043/job/136838259631)，含真实 Chromium | PASS |
| [Agent input and run consistency #44901527091](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527091) | [contracts-and-browser #136838259646](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527091/job/136838259646) | PASS |
| [P10-A native whole-spacecraft power coupling #44901527118](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527118) | native-rw-eps-coupling，234 passed | PASS |
| [Registered simulation parameter surface audit #44901527592](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527592) | registry-form-native-surface，11 passed | PASS |
| [Project script and dependency governance #44901527067](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527067) | governance job 成功 | PASS |
| [Continuous satellite static checks #44901527079](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527079) | syntax-and-contract job 成功 | PASS |
| [Continuous satellite unit tests (without Basilisk) #44901527093](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44901527093) | session-control，268 passed、12 skipped、6 deselected、2 warnings | PASS（已执行部分） |

注意：`.github/workflows/continuous-console-browser.yml` 独立流程仅配置 `pull_request` / `workflow_dispatch`，未出现在这次 SHA 的八条 `push/main` runs 中；该**独立专项流程**状态仍为 **INCONCLUSIVE**，不把其他两个 Chromium 流程冒充为它。

## 2. 必须验收的物理量：该 SHA 的原始运行日志

以下来自原生 job `136838259990` 的 `DT1_*` 测量记录。对应断言固定在该 commit 的测试源码（[四效应联测](../../tests/test_native_integrated_environment.py)、[600s 阻力](../../tests/test_native_orbital_environment.py)、[SRP 本影](../../tests/test_native_srp_spice_eclipse.py)、[PPIGRF](../../tests/test_native_igrf14_ppigrf_correlation.py)），非本报告新增的阈值。

| 条目 | 在 main 上记录的实测数字 | 已执行判据 | 裁决 |
|---|---|---|---|
| 四效应同一真实 Basilisk Task | 120s、12 个有效采样；最低密度 `5.191652725e-11 kg/m³`；最大阻力 `3.092179075e-03 N`；最大 SRP `1.385037897e-04 N`；最小阻力功率 `-22.44079288 W`；Drag A/B `0.2177126328 m`、SRP A/B `0.01000388427 m`、相对旧 J2 的 GGM 差 `0.5282819834 m`、IGRF/WMM 最大矢量差 `5.981208016e-09 T` | Drag/SRP/4阶GGM03S/IGRF14 同时 opt-in；各 A/B 位置差 >0.001m、IGRF/WMM 差 >1e-10 T；正密度/力、负功率、真实地球朝向一致、消息读取错误数=0 | **PASS** |
| 5400 s 全轨道联合效应与跨本影 | 周期 `5396.888537 s`；仿真 `5400 s`、步长 `5 s`、输出间隔 `60 s`；90 个有效采样，其中日照 53、本影 37；最小阻力 `2.823995394e-03 N`、最大阻力 `3.537102855e-03 N`；最大 SRP `1.385037196e-04 N`；本影最大 SRP `0 N`；SRP on/off 终态位置差 `23.44759781 m` | 覆盖 ≥1 轨道、日照 ≥30、本影 ≥10；各采样阻力持续 >0 且功率 <0、本影 SRP <1e-10 N、日照 SRP >0、终态 A/B >0.001m | **PASS** |
| NRLMSISE-00 阻力：600 s 半长轴 A/B | 无阻力 `6647253.359927486 m`；有阻力 `6643441.055106110 m`；**差值 `-3812.304821376 m`**；最大阻力 `0.03725094387 N`、最小阻力功率 `-274.0595737 W`、最大相对速度 `7749.833225185 m/s` | `drag_a < baseline_a`；真实非零阻力、非正阻力功率、有限有效密度；测试输入面积 10m²、Cd 2.2、质量 10kg、600s | **PASS** |
| SRP 单独本影与日照 | 日照最大 `1.408643438e-04 N`，本影 31 个采样最大 `0.000000000 N`；本影轨迹 A/B `0 m` | 本影系数 <0.001，力 <1e-10 N，位置 A/B <1e-4 m；日照力 >0，位置 A/B >0.001m | **PASS** |
| IGRF-14 vs 独立 PPIGRF：2026 | 6 个有效采样，最大残差 `0.05141683810 nT`、平均 `0.04968966981 nT`、RMS `0.04970368537 nT` | 每个位置、相同惯性系的**矢量**残差最大 <1 nT；PPIGRF 2.0.0 代码及 IAGA 原始 SHC 哈希固定；负控 WMM/IGRF 差最低 `3.329853047 nT` >1nT | **PASS** |
| IGRF-14 vs 独立 PPIGRF：2029 | 6 个有效采样，最大残差 `0.03099556718 nT`、平均 `0.03006529458 nT`、RMS `0.03007174652 nT` | 最大 <1 nT，SPICE Earth-fixed 与惯性系真实转换；不允许只比较磁场幅值 | **PASS** |
| GGM03S 资产与显式阶次 | 上游 GGM03S SHA-256 `71f8736f98c5d8c972d0f6d56a2389302f72b8ac7517c605d312162ca446a3a1`；180×180 原始资产，运行显式 degree/order 4；degree2→3 位置差 `87.18134182 m`，degree3→4 `76.07052184 m`（1800s 的专门 A/B） | 完整系数、原生阶次选择、与旧 J2/相邻阶数的非零差异；不等于宣称当前运行使用 180 阶 | **PASS** |

### 物理资产/模型来源边界

- 该 `main` 原生 job 真正安装的是公开发行的上游 `bsk 2.11.0`；不是通过“模拟 Python 物理”代替运行。
- GGM03S 属于固定哈希的原始系数；IGRF-14 采用 IAGA IGRF14 源系数、确定性 COF 转换、官方 Basilisk MAG 计算链；PPIGRF 是**独立数学实现**而非独立飞行实测。
- 本次空间天气的 `source_url` 是 `test-fixture://celestrak-compatible-sample`；卫星质量、参考受力面积、Cd/Cr 等都是测试夹具。不得称为真实航天器参数标定/飞行精度。
- 保留日月/SPICE 与本影现有范围。**没有证据**证明实体表面面片受力、实测热层风、固体/海洋时变潮汐、辐射带或硬件标定已被覆盖。

## 3. 产品路径、Run Bundle 密封性

**PASS — main 原生同 SHA 专项：** `test_four_effects_pass_canonical_taskspec_exported_script_and_sealed_run_bundle` 实际将 canonical TaskSpec 导出为独立进程中的可执行 capability-python 脚本，并创建、执行、验证密封 Run Bundle；输出：
- `script_executed=true`，`bundle_sealed=true`；
- script / bundle 的 `drag_force_max_n` 均为 `3.022097078e-03 N`，容差 `rel_tol=1e-6, abs_tol=1e-12`；
- `bundle_rows=4`；
- `bundle_plan_sha256=cd9c9eb8a82868e859bc303c782a9e52b530bae5ae8a92fff094337193c5d76e`；
- `verify_run_bundle(...)["ok"] is True`，任务运行记录 `SUCCEEDED`、验证 `PASS`。

**PASS — main Agent/Run 负控：** `tests/test_run_bundle_seal_binding.py` 包含文件改写、manifest 重新计算却与 seal 不一致、缺失/损坏 Seal、Run ID 不一致、缺失/路径非法/大小不一致等拒绝用例；同 SHA Agent/Run 主测试组 `520 passed, 3 skipped, 2 warnings`。负控检查的是**已有自动化用例**，非另行破坏真实飞行档案。

**PASS — main 浏览器真实下载校验：** 原生组件/子系统 job 的浏览器导出汇总记录：`physical_sealed_run_bundles=10`、`chromium_downloaded_run_bundles=9`、`sidecar_sha256_checks=9`、`passed=true`。校验含 ZIP 哈希、`SEALED.json`、manifest SHA-256 和运行 ID。**注意**：密封性不是整星 native 中断后 checkpoint/resume 的等价证明；同 SHA 原生 job 的 R9F/R9G 诊断明确报告重启奇异状态及尚未证明 restart parity。

## 4. 浏览器端与回归

- **PASS（已执行覆盖面）：** Agent/Run 在真实 Playwright Chromium 下 `19 passed`；基础 Agent/Run `520 passed, 3 skipped`；另原生组件引导式工作台浏览器 `22 passed, 4 deselected`，其中包含 EPS/Comm 六种组合与整星九类事件 Run。独立下载包校验见上。
- **PASS（静态/控制平面等其他已执行覆盖面）：** 静态 syntax-and-contract、unit session-control、P10-A native power、参数表审计及脚本治理对应全部真实 job success。非原生 unit 为 `268 passed, 12 skipped, 6 deselected`。
- **INCONCLUSIVE（未在目标 SHA 上执行的专项）：** `continuous-console-browser.yml` 的独立控制台浏览器冒烟流程不在这次 main push 的 run 列表里；不能以别的 Chromium 测试对它作 PASS 替代。已执行的浏览器路径 PASS；“浏览器全部功能无回归”的全称断言 **INCONCLUSIVE**。

## 5. 三态总裁决

| 验收主张 | 状态 | 依据与界限 |
|---|---|---|
| 真实 main a5147550 后合并 CI | **PASS** | 八条实际 push/main workflow 逐一成功，同一 `head_sha` |
| NRLMSISE/SRP/GGM03S/IGRF14 四效应同任务及 A/B | **PASS** | 120s + 5400s 的 Basilisk 日志与原有断言 |
| 600s 阻力半长轴差及耗散方向 | **PASS** | −3812.304821376m，力/功率均真实采样 |
| 本影 SRP 为零且日照非零 | **PASS** | 本影最大 0N；<1e-10N 阈值 |
| IGRF14 与 PPIGRF 矢量残差 | **PASS** | 2026/2029 最大 0.05141684/0.03099557nT，阈值 1nT |
| TaskSpec→实际脚本→密封 Run Bundle | **PASS** | 真实执行 + 正向密封校验 + 负向篡改拒绝 |
| 实测浏览器核心引导/Agent/Run 无回归 | **PASS** | 两条独立 main 的真实 Chromium 测试与下载包复核 |
| 未触发的独立 continuous-console-browser 专项 | **INCONCLUSIVE** | 目标提交上缺该工作流的实际执行记录 |
| 泛化到所有浏览器路径、真实飞行标定、整星 checkpoint/restart 完整一致性 | **INCONCLUSIVE** | 不属于已取得的验收证据；R9F/R9G 还明示 native restart parity 未证明 |

**DT-1 当前结论**：在本次定义的“四效应原生联合运行 + 关联物理指标 + 正式产品路径 + 已运行浏览器核心回归”范围内，该 `a5147550` 主干实现 **PASS**；这不等价于全部 LEO-Engineering-R1 阶段出口已验收，更不等价于飞行级高保真认证。

## 6. 后续行动（不在本报告 PR 中提前实施）

1. 若要求独立 continuous-console-browser 测试也必须取得 **exact `a5147550`** 结果，应运行该 workflow 的 `workflow_dispatch` 并**固定选择原提交**（不得用较新的 main SHA 替换），随后将 run id、通过数及截图/导出物增补到此报告；未产生该记录前保持 INCONCLUSIVE。
2. 下一开发片进入 **DT-2 器件级非理想**：在已有 Basilisk IMU / 反作用轮基础上，先完成随机种子复现合同、传感器噪声谱/漂移/量化/安装误差和执行机构死区/时延的可配置、可追溯 A/B；每片一 PR，保留严格负控与定量误差指标，不动 DT-1 基线。
3. DT-2 之后才启动 DT-3 的任务级误差预算、版本化 V&V 门槛、独立对照及证据等级；DT-4 仅继续档1，档2 等实装证据和审批；DT-5 万点绘图、百万轨迹、GPU 和多会话必须分别实测，软实时不宣称硬实时。
4. #207 的参考几何仍是档1，不允许反推安装坐标、尺寸、轴向；16 类未定位设备不画；#206/#208/#210/#211/#212 按用户当前指令保持 OPEN，不在本报告 PR 中裁定关闭。

## 7. 开发经验

1. **必须将主干提交与源 PR 隔离**：选择实际 `head_sha` 和 `event=push` 的运行证据，避免“PR 绿过”偷换主干验收。
2. **工作流 green ≠ 物理 PASS**：从实际 job log 的 `DT1_*` 行逐条摘取数字，同时核对源码断言及数值单位；对缺失的独立工作流单独给 INCONCLUSIVE。
3. **密封性、恢复性分开**：Manifest/Seal/ZIP 哈希通过不意味着原生动态系统可以无损跨进程重启。
4. **量测值 ≠ 飞行认证**：使用有来源的离线 fixture 与显式人工物理输入，不将测试效应放大解读成真实硬件标定或任务可信度提升。
