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
