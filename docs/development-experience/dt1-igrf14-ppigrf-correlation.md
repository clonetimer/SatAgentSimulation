# DT-1 IGRF-14 独立 PPIGRF 数值相关：限定独立性与准确性边界

日期：2026-11-21。试验分支以 PR #211 精确 HEAD `7686478ab8c5fc8ee70b640f55bf3c530734077c` 为基底，**不修改 #211**，不涉及 `main` 或其他未合并物理 PR。

## 为什么做独立数值参考

已有 #211 证明真实 IGRF-14 系数经过 Basilisk 原生地磁模块，并影响磁强计 `tam_S`，但只提供 WMM 与 IGRF 的相互差异；两者有差异不等于实现正确。

采用 **IAGA V-MOD `ppigrf==2.0.0`**、Git Tag commit `9f3f2f726e2d3a5367fe6685036c8ce3af66c185`，固定纯 Python 计算代码 Git blob `2459c72360fe2446449259088ab36625b70fb613`，以及独立发布 IGRF14.shc Git blob `782112655bfab891f8d24d3b2a0225e72f0ad6fc`（42,115 字节）。此纯 Python 球谐实现不同于 Basilisk 官方 NOAA MAG C 内核，避免同一算法自比；但**使用同一 IAGA IGRF14 标准系数**，不是独立飞行测量，也不是 BGS/NOAA 在线服务的响应数据。

## 坐标和验证

- 不重算 Basilisk 地磁的球谐模型。运行 Basilisk 全整星并取**真实**空间位置 `orbit.position_m_x/y/z`、磁场 `adcs.magnetometer.reference_n_tesla_*` 和新增只读 `SpicePlanetStateMsg.J20002Pfix/PositionVector`；Earth-center subtraction 后用 SPICE J2000→P DCM 转成地固三维坐标。
- 以真实 Earth-fixed 地心球坐标 `r[km]`、余纬 `theta[deg]`、经度 `phi[deg]` 和 UTC `epoch + time_s` 交给 IAGA PPIGRF 计算 `Br, Btheta, Bphi`（nT），通过三组球坐标基向量转换到 P，再乘 `J20002Pfix.T` 回到 N（T）。
- 两个不同历元 2026-01-01、2029-02-01、不同轨道倾角和升交点，逐点比较 t=10..60 s 共 12 个实际传感器场位置（t=0 排除 Basilisk 初始化样本）。**所有**实际原生磁场矢量与独立 PPIGRF 的范数残差要求小于 1.0 nT，保留每组最大、均值和 RMS；拒绝静默把 WMM 源错称 IGRF（负控需超过该 1 nT 阈值）。
- 框架支持 Linux CI pip 固定 `ppigrf==2.0.0` 和 `pandas==2.3.3`，但仅在物理参考计算步骤安装/使用。生产仿真和网络权限不受该依赖影响。

## 不改变的物理约束

- 原先的 `WMM2025` 默认、IGRF14 源文件与派生 COF SHA、传感器参数、运行时积分步长、原有测试全部不变。
- 新增的 `environment.igrf14.earth_* ` 均是 IGRF 显式模式下的只读 SPICE 消息观测字段，不新增动力学模块。
- 这只是**跨数值实现的软件一致性相关**。不宣称 IGRF14 模型不确定性、外磁场扰动、实际地磁观测误差、空间辐射环境或飞行级磁强计标定已得到验证。
- 同时修复/回归风险：跨 PR #206/#208/#210 的整星组合是否兼容仍需要另一个 exact-head gate。

## 经验记录

保留失败证据和残差本身，出现坐标、单位、时间、系数版本不符时先核对来源，不允许放宽误差阈值或者改物理参数制造通过；任何变化必须重新跑真实原生 CI。

## 首轮真实 CI 的来源差异与处理

- Run `44784554104` 的独立对照未进入磁场计算：安装的 PyPI `ppigrf==2.0.0` 源码 `ppigrf.py` Git blob **与官方标签一致**，但其默认内置 `IGRF14.shc` 的 Git blob 为 `6c6c817e9c3a2ad8ebd5180aa4a9ce5685d3125a`，与 IAGA V-MOD `IGRF14eval` 正式发布文件 `782112655bfab891f8d24d3b2a0225e72f0ad6fc` 不一致，来源锁定失败是正确行为。
- 不更改 `ppigrf` 计算函数或接受非原始系数，也不伪称 wheel 自带数据是正式文件。单独保留 IAGA 官方 `data/release/IGRF14.shc` 原始 42,115 字节于 `tests/fixtures/IGRF14-IAGA-final.shc`，每次真实测试用 Git blob SHA 验证后，**通过 `coeff_fn` 明确传入独立计算器**；这样既隔离默认 wheel 数据，也不依赖额外 CI 在线下载。
- IAGA-VMOD/IGRF14eval 原仓软件/文件附 MIT / CC-BY 4.0 授权声明，来源及不可变 commit 保留；本文件是测试/审计参考数据，非运行时资产。
- 另一个无关 CI 失败为 P10-A 运行准备从公共服务获取资产时 HTTP 503 `Backend.max_conn reached`。这不是磁场计算失败；仅在新的 exact-head CI 中真实重试，不能解释为 PASS。
