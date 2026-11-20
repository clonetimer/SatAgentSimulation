# DT-1：官方完整 GGM03S 二、三、四阶重力系数原生验收

工程边界：`main` 的 `spice_wmm` 默认仍使用稀疏 `GGM03S-J2-only.txt`。本阶段仅新增显式选项 `earth_gravity_model`（legacy_j2_only / ggm03s_degree2 / ggm03s_degree3 / ggm03s_degree4），不改变默认质量、惯量、面积、积分步长、磁场和 SPICE 资产。

## 来源锁定

- 上游：`AVSLab/basilisk` v2.11.0 对应源码提交 `6c1511f5385dccaa73c9ae24522a74adc24fe623`。
- 原始文件：`supportData/LocalGravData/GGM03S.txt`；Git blob SHA-1 `6022af24f8aaeba461eb6a3c22330265578a8710`；1,350,695 字节。
- 仅由显式安装脚本 `scripts/fetch_ggm03s_gravity.py` 获取（支持 `--offline`）。运行时不联网；按源 Git blob 身份、字节数、归一化标志、参考半径/GM 和实际完整 n=2,3,4 系数行审核。记录完整文件 SHA-256；不从 J2-only 文件补零或凭字段 maxDeg 伪造 J3/J4。
- 原始 GGM03S 的 C20 为 zero-tide 约定、标称参考历元 2005；当前静态系数不等于随时间变化的固体潮、海潮或任务精轨标定。完整 degree3/4 **包含 tesseral 项**，因此 `degree2 -> degree3` 和 `degree3 -> degree4` 是完整阶次截断差，并非纯粹单项 J3/J4 zonal 隔离。

## 动力学证据门

- 必须使用官方 Basilisk 2.11.0 `earth.useSphericalHarmonicsGravityModel` 接入同一个 `UnifiedNativeTask`；只有实际 `spice_wmm` 运行且完整资产校验通过才允许选择 `ggm03s_degreeN`。
- 在真实 SPICE Earth `J20002Pfix` 旋转矩阵下运行 degree2/3/4 的相同初始轨道；从消息记录矩阵随时间变化、正交性及行列式，不将高阶项当作惯性静止地固场。
- 比较四种实际输出：原始 J2-only、完整 GGM03S degree2/3/4。真实度 3 和 4 的加入须对末端轨道位置、速度产生非零独立增量；比较 10/5/2.5 s 步长收敛（不是改变运行默认步长）。
- 所有来源、哈希、系数、矩阵与位置/速度残差以 CI 数值记录为准；不能把本测试冒充 GMAT/STK 独立轨道相关、飞行实测校准或精密时变潮汐模型。

## 本阶段产物

- `src/sat_sim/bsk_engine/gravity_harmonics_asset.py`：原始 GGM03S 数据强校验；
- `scripts/fetch_ggm03s_gravity.py`：仅在安装/CI 阶段下载，运行时不访问网络；
- `src/sat_sim/bsk_engine/orbital_environment.py`：显式模型选择与来源记录；
- `tests/test_native_ggm03s_gravity.py`：负控、真实 SPICE Earth 方向、度 2/3/4 轨道响应和数值收敛；
- `tests/test_parameter_surface_audit.py`：注册参数计数与真实物理选择引用。

## 记录：CI 首轮治理反馈

- 物理专项 `Verify actual GGM03S degree2/3/4 forces, rotating Earth and refinement` 已 PASS，确实运行了官方原生 Basilisk；正式结论仍取决于新 HEAD 全部 CI。
- 治理 Run `44776627021` FAIL：`SCRIPT_HEADER_INCOMPLETE`，新增 `scripts/fetch_ggm03s_gravity.py` 缺少仓库约定的“用途／参数／输出”文件头。已读取报告中的单条失败记录。
- 本提交只补全脚本中文文件头，不变更数据源、SHA、模型、默认值、力场阶次或物理判据。
