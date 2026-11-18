# P9-N4-D：完整 Run Bundle 导出开发经验

日期：2026-11-19
状态：已合入 main（PR #182）；官方 Basilisk 2.11.0 API/物理合同与 Chromium 九类单机下载验收 PASS。

## 为什么需要这一步

此前工作台有两个相近但不等价的导出入口：

- `导出数据集` 只打包 `results/dataset`。
- `诊断包` 由 `/diagnostics/bundle?run_id=...` 生成，只包含白名单中的 Run 证据子集和系统诊断信息。

两者都不能替代完整的、可离线复核的 sealed Run Bundle。因此本轮不扩展数据模型或执行链，只新增一个基于现有 `bundle_manifest.json` 与 `SEALED.json` 的完整下载接口。

## 实现

- API：`GET /runs/{run_id}/bundle/download`。
- 先调用 `verify_run_bundle`，只允许 sealed 且完整性校验通过的 Run。
- 校验 URL Run ID、manifest Run ID、seal Run ID 和 manifest SHA-256 的一致性。
- ZIP 包含原始 `bundle_manifest.json`、`SEALED.json` 以及 manifest 中列出的全部文件；不打包 `runtime/execution.lock`，不包含 manifest 外的临时文件。
- 打包每个文件时流式计算 SHA-256，并与 manifest 中的预期值比较；如果文件在校验与打包期间发生变化，则中止并删除临时 ZIP。
- 路径限制：Run ID 必须是单个可移植路径段；manifest 路径先由既有 `verify_run_bundle` 验证，再通过 `_safe_artifact` 限制在 Run 根目录内。
- 临时 ZIP 通过 `FileResponse` 返回，并由 `BackgroundTask` 在响应完成后清理。
- UI 新增“导出 Run Bundle”，仅当当前 Run 同时满足 `sealed=true` 与 `integrity.ok=true` 时显示；现有数据集 ZIP 和诊断包入口保持原义。

## 验收设计

1. API 正常路径：对官方 Basilisk 原生反作用轮 Run 下载 ZIP，验证 manifest、seal、Run ID 和每个文件的 SHA-256。
2. 未封存拒绝：仅 `prepare_run`、尚未执行和 seal 的 Run 必须返回 HTTP 409 / `RUN_BUNDLE_NOT_VERIFIED`。
3. 篡改拒绝：修改 sealed Run 中的 `results/summary.json` 后，下载必须返回 HTTP 409，不得返回部分 ZIP。
4. Chromium：对向导中的反作用轮、IMU、星敏、磁强计、电池、太阳翼、推力器、贮箱和热节点逐一真实运行，从结果面板点击导出，并核验 ZIP 中全部 manifest 文件哈希。
5. OpenAPI 与 release manifest 声明新路由；UI 合同测试确认按钮和下载入口存在。

## 关键工程经验

1. **不能把诊断包误称为完整 Run Bundle。** 诊断包是经过筛选和脱敏的证据子集；完整 Run Bundle 导出必须保留原始 manifest 与 seal。
2. **不能只在 ZIP 生成前做一次完整性检查。** 文件可能在检查后、打包前变化；因此每个文件在流式写入 ZIP 时再次计算 SHA-256。
3. **manifest 是导出边界。** 不能直接 `rglob('*')` 打包整个目录，否则临时文件、执行锁或未登记文件可能进入交付包。
4. **未封存 Run 不提供完整导出。** 不能把 PREPARED/运行中/完整性失败的目录包装成可复核的正式交付物。
5. **UI 只负责呈现资格，不替代 API 校验。** 即使用户通过脚本直接请求 endpoint，服务端也必须独立执行 seal、manifest、路径与 hash 检查。
6. **复用现有 Run Bundle 体系。** 本轮不新建 Export Store、第二套 manifest 或另一条执行链。

## 最终同 head 验收与合并（2026-11-19）

- PR #182 head：`d2efa4756c9b47c605a09e59495e3583f712bf57`；Squash merge：`7a90ef2b85db38cb8b64cc5ef4f72462f98a1d46`。
- 官方 Basilisk 2.11.0 专项 workflow #44349903334 PASS：RW 18、sealed TaskSpec Run 1、传感器 26、EPS 24、推进/贮箱/热节点 218、模板/API/Schema 14、native preflight 8。
- Chromium 专项 4 passed / 4 deselected；两个 guided native browser journeys 覆盖九类单机，逐一从结果面板下载 Run Bundle，并核验 ZIP 成员、manifest/seal 绑定和所有文件 SHA-256。
- API 正常导出、未封存拒绝、篡改拒绝、Run ID 与 manifest/seal 绑定均通过。
- 证据 artifact `native-component-physical-evidence`：ID `14448486528`，12,822 bytes，SHA-256 `60cb884de58b6d66607265023854cd806d4c519b5453c13cdaf1e0f9fbc96a33`。
- 合并后 main 的完整 Run Bundle 下载入口已可用；这项工作只增加导出能力，不改变物理模型、执行链或数据模型。
