# DT-1：IGRF-14 原生地磁场与磁强计 A/B

**边界：** 保留 `spice_wmm` 的默认 WMM2025，不静默替换。新显式 `magnetic_field_model=igrf14` 仅用于 `spice_wmm`，仅支持 2025–2029 UTC。IGRF-14 不是 Basilisk 2.11.0 的独立官方 C++ 模块——本阶段复用其官方 `MagneticFieldWMM` 所用 NOAA MAG 球谐计算库，**显式用 IGRF-14 源系数代替 WMM2025**，并记录不同的 model_id、来源和转换摘要。

## 确切源

- IAGA V-MOD 官方最终发布：`IAGA-VMOD/IGRF14eval` 源码提交 `2899280ffbec7cae12e19fbcaa04ed9609092729`，`data/release/igrf14coeffs.txt`。
- 原始 Git Blob SHA-1 `13e7122692044f292afcdf9cac03c017837fa064`，42,411 字节；保留原文件，校验每一阶（n=1..13）每一阶次 m 的 g/h 共 195 条、2025 主场、2025-30 SV（n<=8）。
- 固定锚点 `g10=-29350.0 nT`、`g11=-1410.3 nT`、`h11=4545.5 nT`、`g10SV=12.6 nT/year`。源文件系数 Schmidt 半归一化，地磁参考半径 6371.2 km，与 NOAA/IAGA IGRF-14 文档一致。
- 脚本 `scripts/fetch_igrf14_asset.py` 仅在安装/CI 读取固定源码提交，检查 Git blob 和原始长度；确定性转换为供 Basilisk NOAA MAG 引擎读取的 13 阶 `.COF` 格式。运算仍由 Basilisk 原生库执行，**不是自写 IGRF 场模型**。
- `--offline` 模式只验两个已有文件，运行时检验原始 source SHA、精确 COF 内容及其 SHA-256；任一缺失/篡改 fail closed，不能回落到 WMM。
- IGRF-14 是内源主磁场参考，不等同于磁暴、近地空间磁扰动、高能粒子环境或真实磁强计标定。

## 实测关

`tests/test_native_igrf14_field.py` 需实测：
1. 参数非法、缺源、源文件改动、COF 被篡改全部拒绝；
2. 默认与显式 WMM2025 输出在同一 Basilisk 环境中一致；
3. 同日期、同初始轨道，官方 Basilisk `magneticFieldWMM` 计算出的 IGRF14 与 WMM2025 地磁惯性系矢量存在可测差异，**真实 Basilisk Magnetometer 的 `tam_S` 同时改变**；
4. Run Summary 记录 IGRF14 来源 SHA、派生 COF SHA、版本、有效期和计算内核；2025–30 模型超期拒绝。

日期 2025.0–2030.0 的 SV 采用 IAGA 预测，仅用于工程环境；不把 WMM 结果重命名，不宣称与独立 NOAA/BGS 数值计算器已相关或飞行级可靠性。
