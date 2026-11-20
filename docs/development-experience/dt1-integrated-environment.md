# DT-1 四项物理能力联合集成证据（与原始 PR 分离）

本阶段创建独立的 **integration-preview** 候选，不自动合并 main 或修改已独立通过的 #206、#207、#208、#210、#211、#212。基底为 #209 已验证的 NRLMSISE-00 Drag + SPICE/Eclipse SRP 组合 HEAD `316d7b5996da7382feab8ce499651cd78553ed18`。

合成的运行图一次性包含：
- 官方 Basilisk NRLMSISE-00 `MsisAtmosphere` + `DragDynamicEffector`，准确读取机体系力 `forceExternal_B`，离线气象资产来源显式标为 `test-fixture://`。
- 官方 `RadiationPressure` 接真实 SPICE Sun/Eclipse，准确读取惯性系 `forceExternal_N`。
- 完整官方 GGM03S 球谐系数的显式 2/3/4 阶选项；默认仍是 J2-only，未把 J2 稀疏源当作全系数。
- IAGA 固定 IGRF-14 真实系数经确定性转换的 NOAA MAG 计算模型，真实 `Basilisk Magnetometer` 接磁场消息；默认仍是 WMM2025。
- 参考校验器 IAGA V-MOD `PPIGRF 2.0.0` 与正式 IGRF14.shc，作为仅 CI 数学相关试验，与物理主运行模块解耦。

## 验收流程

`tests/test_native_integrated_environment.py` 在真实 SPICE Sun 初相位下，使用同一初始轨道跑全部四项启用，以及分别关 Drag / SRP / 高阶重力 / IGRF 的四个对照，检查原生消息链与运行图、密度、能量耗散符号、光压方向、两组 SPICE Earth 朝向一致性、各种物理 A/B 的真实差值。所有物理数据来自原生 Basilisk 运行 Trace 和原生 Run Summary，不写后处理动力学。

此外**同一候选 HEAD**必须通过 #206、#208、#210、#211、#212 已有的各自原生测试，独立 PPIGRF 实施一致性（<1 nT）、脚本治理与整个长时回归，不能用先前分支 PASS 代替组合版本验收。

参数注册目标 `177`：原有 169 + Drag 三个 opt-in + SRP 三个 opt-in + GGM03S 选择 + IGRF14 选择，既有 169 默认参数、默认物理模型、积分步长及已有断言不变。

## 保留边界

所有质量、面积、反射系数、空间天气和轨道时长均为**明确测试输入**，不是 CAD 实装、物理标定或飞行精度证据。缺少高保真面片受力、地磁暴外场、固体/海洋时变潮汐、精确热层风及独立飞行数据相关。
几何参考 PR #207 不混入此次物理运行时集成，P9-M4 的映射晋升禁令不触动。

## 开发经验

1. 先在 #209 验证 Drag+SRP，再复用其实际分支 HEAD 而不是把两个 PR 的通过率简单加总。
2. 对共享源码 `orbital_environment.py` 明确合并 Drag、GGM03S、IGRF14 三条条件数据流，保留所有 fail-closed 门禁；原始默认路径不增加新必选资产。
3. 对共享 `unified_native.py` 的 GGM 与 IGRF 位置/姿态记录保留独立字段和来源，以真实 SPICE 消息验证二者一致，不在仿真之后伪造坐标转换。
4. 合并后的**全部物理原生数值和其他 CI 状态**未验收前不得标记为 PASS；如果 CI 失败先记录真实根因，再做不降低标准的最小修复。
