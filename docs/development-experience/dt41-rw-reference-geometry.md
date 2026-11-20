# DT-4.1 档 1：BIRDS-4 RW 独立参考外形图层

日期：2026-11-20。仅用于几何对齐演示；不建立实装映射，不改任何 Basilisk 物理参数或积分步长。

## 可核验来源

- CI Run: `43692477458`，artifact `birds4-fm-cad-reference-43692477458`（ID `14153195945`）。
- 只提取 `build/birds4/output/B4_RW_Assembly_v55.glb`，不是从整星 `BIRDS4_FM_Final_v7.glb` 截子树。
- STEP 来源提交 `3bab47a0f53410ef79005d21321ff659726f0a1c`；单独子装配 1,410,016 字节，SHA-256 `3019d97d4990553bcfab39d9cbe0ea15bad9a294f517481b65df1ec71ce4d795`，25 nodes / 24 meshes。
- 单一轮体及其支架变体、紧固件不等于多轮实装；多轮仅是按已校验 TaskSpec 的 `rw_mount_b_*` 与 `rw_axis_b_*` 复制显示。
- GLB 世界系已记录的自转方向 +Y；几何源整体保持原尺寸，不根据惯量估算任何长度。为演示对齐仅以 GLB 包围盒中心视觉归中，**这不是轴心安装基准**。
- `*.assembly-tree.json` 的 `world_transform_m` 未正确合成父链：本图层完全不读取它。既有 P9-M4 参考候选注册禁令保持有效。

## 功能边界

- 图层默认 OFF；显式开启后先请求同源只读端点 `/assets/reference-cad/birds4/rw.glb`，仅从预置 `build/birds4/output/B4_RW_Assembly_v55.glb` 读取。若为 wheel 安装部署，也可通过 `SAT_SIM_BIRDS4_RW_GLB` 指向同一固定文件。
- 本地缺失 404、大小错误 409、SHA-256 错误 409。浏览器再核验文件大小、SHA-256、glTF 格式及节点/mesh 数，不符合立即拒绝显示；人工选择 GLB 同样需固定摘要。
- 独立 Three.js 参考 Group，不改变已有配置几何 Group。轮速来自同一时间精确配对的 `wheelPhases`，只更新单独的视觉相位指针，不旋转支架或把该视图解释为真实机械转子；无有效样本就不合成角运动。
- 源场景外形保持 1:1 米单位；叠加位置与姿态仅从已校验的 TaskSpec 取得。工程分解视图的偏移不影响物理安装值。
- 免责提示常驻；不建立 CAD/物理实体 as-built、selector promotion、GLB 世界变换到 B 系、真实几何轴/物理量反演或遥测绑定。

## 安装与验收证据边界

从既有 GitHub Actions artifact 仅恢复上述文件到同名相对路径（或配置只读绝对路径），**运行时不下载网络文件**。浏览器可离线加载用户选择的同一 SHA-256 GLB。

CI 测试验证缺失与篡改均拒绝访问、原生页面默认关闭、真实 standalone GLB 三轮 TaskSpec 布局与截图、篡改 GLB 拒绝、离开/关闭图层不改变仿真输入。CI 将工件作为测试前置条件且只提取独立 RW GLB；不能把对固定工件的界面验收冒充实装映射。

## 开发经验

1. 使用已转换的独立子装配而非 61 MB 整星 GLB，避免把整星 CAD 的部件索引误作子装配索引。
2. 用后端白名单文件端点 + 浏览器二次摘要校验，防止错误 GLB revision 的位置/尺寸看似合理。
3. 物理轮体的动画与 CAD 外观明确分层：已知源 GLB 不给出可审核的转子/支架机械运动学拆分，因此仅做旋转相位视觉标识，避免将整个固定支架一起转动。
4. 多轮复制唯一位置源是 TaskSpec；任何 GLB 世界变换均不进入仿真参数或安装审批。
