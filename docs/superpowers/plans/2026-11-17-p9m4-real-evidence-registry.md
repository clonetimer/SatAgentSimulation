# P9-M4-A 真实证据注册与映射隔离实施计划

> **For agentic workers:** Use superpowers:executing-plans to implement task-by-task.

**Goal:** 将真实 M3 审查包、工程对象树、STEP 装配树及 GLB 转换报告接入 M4，保留参考 CAD / 未定位状态。
**Architecture:** 在既有验证器前补充严格类型/视图/哈希检查；独立只读注册器校验来源、文件字节和节点摘要后生成映射。保留原模型与遥测协议。
**Tech Stack:** Python 标准库、pytest、GitHub Actions。
**Spec:** `docs/superpowers/plans/2026-11-17-p9m4-unified-asset-mapping.md`。

## Global Constraints

不自动审批实装证据，不创建实装实例或安装变换。STEP occurrence 路径不等于 GLB 节点索引；未完成两者对应前不启用逐节点渲染或遥测绑定。契约声明校验不等于批准记录认证。

## Review Focus

拒绝跨类型视图混用、字符串 views、非法哈希；拒绝跨 Run 遥测；拒绝未知工程对象和重复节点；拒绝篡改 GLB/审查包来源；零候选或来源不一致不能报告成功。

### Task 1: 收紧声明契约
- [ ] 为公开验证函数添加负向测试并观察失败。
- [ ] 新增 `engineering_mapping_policy.py` 并接入既有验证器；保留原 11 项行为测试。
- [ ] 验证非实装类型不能进入实装视图，输入类型/哈希/遥测作用域均显式校验。

### Task 2: M3 → M4 只读注册
- [ ] 添加注册器测试：真实输入、确定性、来源错配、数据篡改、非设备对象、缺失证据。
- [ ] 实现 `engineering_reference_registry.py`，输入 review/object-tree/assembly/conversion/glb，输出 M4 bundle + validation。
- [ ] 校验实际 GLB SHA-256、STEP 源版本、源节点（含局部/全局矩阵）的证据摘要。
- [ ] 对当前固定输入产生 3 reference_cad + 16 unlocated，0 as-built，0 telemetry binding，0 node-renderable。

### Task 3: 可复验交付
- [ ] CI 在 Ubuntu / Windows 执行契约测试；独立真实证据任务固定 artifact run/id/digest 并运行注册器。
- [ ] 输出审计 JSON；记录开发经验；同一提交检查完成前不宣称完整 M4/UI/长时回归通过。

## Execution ruling

当前运行容器不能解析 github.com；本地使用连接器读取且 Git blob SHA-1 校验一致的相关模块/测试快照，CI 使用完整仓库。没有修改用户 main，也不以局部运行替代全仓验收。暂不做 GUI/实际坐标审批，先完成可复用的真实证据输入层。


## P9-M4-B：只读映射状态接入卫星场景

- 新增 \`GET /workbench/engineering-mapping\`，仅从部署环境变量 \`SAT_SIM_ENGINEERING_MAPPING_BUNDLE\` 指定的本地只读文件加载映射清单。
- 每次请求均重新执行统一映射契约校验；未配置返回 \`not_configured\`，文件缺失或声明不合法返回 \`invalid\`，不回显本地路径或原始证据。
- 场景检查器显示四类映射计数及当前所选对象的映射类型。
- 当前不加载 GLB，不推断 STEP occurrence 到 GLB node selector，不创建逐节点渲染、遥测绑定或安装坐标。
- 无映射文件部署时明确显示“未配置”，不以硬编码的 3/16 数量冒充运行时数据。
- 新增 API 状态测试与前端面板静态契约测试，并纳入 Ubuntu/Windows M4 工作流。

Stable 工作流另外修复了旧 Basilisk pin 硬编码和 \`/tmp\` 工作目录读取 \`pyproject.toml\` 的问题；版本比较改为项目声明与 wheel 元数据逐项一致。


### Runtime deployment guard

The scene-facing API now requires both:
- \`SAT_SIM_ENGINEERING_MAPPING_BUNDLE\`: absolute path to the generated registry JSON;
- \`SAT_SIM_ENGINEERING_MAPPING_BUNDLE_SHA256\`: SHA-256 of that exact JSON file.

The API verifies the byte digest before parsing, then requires the registry scope \`reference_registration_only_not_as_built_approval\` and enforces zero as-built instances, zero telemetry bindings and zero node-renderable candidates. This prevents a generic or hand-edited mapping bundle from being presented as the accepted P9-M4 reference registry. The CI deployment receipt must supply the JSON SHA-256; neither the file path nor raw evidence is exposed by the API.
