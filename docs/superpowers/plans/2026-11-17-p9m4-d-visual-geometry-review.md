# P9-M4-D：工程几何候选可视化审查工作区

日期：2026-11-17

## 目标

将 P9-M4-C2 产生的真实 STEP occurrence → GLB assembly-group → mesh-descendant 候选，接入现有 BIRDS-4 Reference CAD 浏览器，形成可追溯、可人工检查、不可自动批准的几何审查流程。

## 输入事实与范围

- 当前真实注册产物：GLB scene node inventory 为 852 nodes / 1 scene。
- STEP 侧源装配树的计数与 GLB 导出后的 node inventory 属于不同命名空间，不得混为同一个数量。
- 当前三个候选：battery group node 545（14 个 mesh descendants）、reaction-wheel group node 560（24 个）、payload group node 759（1 个）。
- 所有正式 glb_node_binding 仍为 null；selector promotion、renderable、as-built、telemetry binding 均为 0。

## 实施设计

1. API 只返回经过 bundle SHA-256 与 mapping contract 校验的最小审查投影：GLB SHA-256、GLB node count、reference candidate 的 group node index/name、mesh descendant node index/name/mesh index、候选状态和人工审查标志。不返回原始 geometry_asset、安装变换、证据文件路径或遥测绑定。
2. API 必须拒绝 selector_promoted=true、错误候选状态、越界 node index、重复 descendant index、无效 mesh index、几何节点数不一致等记录。
3. 浏览器本地读取用户选择的 GLB，在 Web Crypto 可用时计算完整文件 SHA-256。只有本地 GLB digest 与 API 证据 digest 完全一致，才启用“定位候选组”。
4. 定位动作只在已加载 Reference CAD 场景中选择并聚焦对应 group node；不隔离/隐藏其他节点，不自动修改 GLB，不创建 formal selector，不进入仿真实装视图。
5. 候选卡片显示设备名称、组节点 index、几何节点数量和 descendant mesh 清单。点击定位后复用既有 inspector，显示装配路径、包围盒、局部 TRS 和根坐标 TRS。
6. 缺少 Web Crypto、digest 不匹配、node index 不存在、候选证据不完整时均 fail closed；仍允许用户手动浏览整个参考 CAD，但禁止依据不匹配的证据索引定位。

## 验收

- API endpoint 单测：unlocated-only bundle 保持兼容；reference candidate 返回最小审查投影；promotion、digest、index、descendant 不一致全部拒绝。
- 前端契约：审查面板存在；客户端 SHA-256 必须先于 candidate focus；候选按钮只有 digest match 后可用。
- 跨平台：Ubuntu 24.04 / Windows 2022 契约测试与 JS 语法检查。
- 后续独立浏览器验收：使用与注册产物 SHA 完全一致的 GLB，逐一定位 battery / reaction wheel / payload，并保存 Chromium 截图和选择证据。

## 明确不在本阶段

- 不将 Reference CAD 转为 As-built。
- 不自动认可候选与真实硬件外形一致。
- 不从 GLB 坐标推导航天器 B 系安装变换。
- 不把 CAD node index 绑定实时 Basilisk 遥测。
- 不以 AI 生成模型替代缺失的工程尺寸、接口或安装证据。
