# DT1-A1 NRLMSISE-00 整星阻力接入：开发经验与验收边界

日期：2026-11-20
关联：PR #206；计划调整 CHG-DT0-010、CHG-DT0-011、CHG-DT0-012
状态：候选实现，原生阻力 A/B 与最终 CI 待验收；本文不作为通过声明。

## 做了什么

在已有 whole_spacecraft.unified_native.v1 的 spice_wmm 轨道环境之上，按显式 opt-in 加入 Basilisk 2.11.0 官方 SpaceWeatherData、MsisAtmosphere、ZeroWindModel、DragDynamicEffector，避免并行重写大气公式。建立本地 CelesTrak 格式 CSV 及 SHA-256/有效日期/来源检查；将 23 路空间天气、密度、温度、本体系力、相对速度和耗散功率送入同一 Run 的证据。R0 不启用大气阻力，默认行为保留。

## 踩坑与改正

1. 首轮 native CI：密度非零、阻力模长为零。发现读取了 dragDynamicEffector 不会更新的继承字段 forceExternal_N，而官方 cannonballDrag() 写的是 forceExternal_B。改用后者计算范数、记录实际本体系力，不凭空转为惯性 XYZ。
2. “力非零”仍不足以证明物理效果。加入 F_B·v_B<=0、相对速度非零和 600 s 的有/无阻力同初始条件轨道半长轴 A/B；待真实 CI 核对结果。
3. CelesTrak 格式兼容不等于真实 CelesTrak 数据。新环境资产清单按真实下载、显式声明、未知来源分别记录；合成夹具明确使用 test-fixture:// 标识。Manifest 的 SHA-256 仅证明本地文件绑定，不是上游数字签名。
4. 原生参数增加后注册表从 169 变成 172，必须更新参数表一致性测试而不能跳过治理；脚本需补用途/参数/输出中文头及 SCRIPT_INVENTORY 记录。
5. 上游 SpaceWeatherData 在缺日期时可能沿用旧值，所以运行前检查 epoch 前三日到结束日逐日覆盖。缺数据、错误磁场档位或 manifest 指纹不符必须失败关闭。

## 当前物理与产品边界

- 真实整星路径为官方 Basilisk 模块与项目模块组成的统一 Process/Task；固定面积和 Cd 是工程假设，不是 CAD 真实投影面积。
- ZeroWindModel 使用地球共转速度，不含热层风场。
- 内部 A/B 证明动力学效应，不等于 STK/GMAT 独立比对，也不等于任务级精度或硬件/飞行标定。
- DT1-A1 只有在该 PR 最终 head 的真实原生 CI、参数/治理/下游回归通过后才允许合并；若失败，必须逐项定位根因而非修改验收门槛。

## 后续

优先收敛 PR #206 的同一提交物理 CI；通过后再考虑 J3/J4、真实 SRP/Eclipse 耦合以及前端联动。不要在本阶段开始新的高保真模型战线。
