# P9-B：独立效能评估工作区与配置反馈闭环

## 目标

把 P9-A 已验证的只读评估函数接入真实 Run Bundle、独立评估报告存储和用户工作区。仿真执行、评估计算、配置修改保持三个独立事务。

## 依赖基线

- P9-A：对象树 v2、24 类组件命名字典、evaluate_snapshot()、object-workspace.js。
- Run Bundle：`run_root/<run_id>`；遥测读取继续复用现有 `/runs/{run_id}/telemetry/overview`、stream 和结果归档。
- 当前只读评估函数不导入 runner、queue、session，不接受客户端上传的“已封存真实快照”作为真实性证明。

## 关键架构决定

### 1. 快照由服务端从已归档运行生成

评估 API 只接受 `run_id`、评估方案和已记录字段引用，不接受客户端任意提交原始 snapshot 来冒充可信运行数据。

服务端必须：
- 通过安全的 `_safe_bundle` 定位 Run Bundle；
- 检查运行记录状态与验证结果；
- 从已归档的真实遥测文件读取指定字段和时间窗；
- 从参数/输出字典核对 owner、capability、source field、unit、dtype；
- 生成 sealed snapshot，计算 source digest；
- 缺失数据、运行未完成、验证未通过、字段未记录时拒绝或返回 NOT_EVALUATED，不补零、不插值、不伪造。

### 2. 评估报告独立、不可覆盖

- 报告以独立文件或现有受支持的持久化存储保存，不写入原始 Run Bundle 内容，不修改运行状态。
- 报告唯一键包含 report_id、run_id、snapshot_id、plan_id/version/digest。
- 相同运行和方案可幂等返回相同报告；新方案版本生成新报告。
- 报告保存采用原子写入与路径安全校验；禁止 path traversal。
- 删除/撤销报告如无审计合同，首版不提供物理删除，只允许标记隐藏/撤销并保留来源记录。

### 3. 评估方案显式版本化

- 方案定义指标、来源对象、source field、unit、formula ID、窗口、采样要求和可选 limits。
- 公式只允许白名单 ID；不接受 Python、JS 或任意表达式。
- 不设置默认合格阈值，不自动生成“优秀/合格”总分。
- 只有用户/开发者明确给出判据才输出 WITHIN_LIMITS / OUTSIDE_LIMITS。
- 评估指标按对象 owner 组织，可包含跨对象关系指标；不能把链路指标错误归到发射机单机。

### 4. 配置反馈是人工确认的新版本

- 评估报告提供“根据此结果调整配置”入口。
- 只把明确映射到原始参数 schema 的建议项复制到新配置草稿。
- 绝不自动改写已提交任务、原 TaskSpec 或历史 Run Bundle。
- 用户确认后调用现有任务版本/配置校验流程；未通过原有验证不得启动新运行。
- 记录来源 report_id、原 run_id、新配置版本和用户确认时间，形成可追溯关联。

## API 契约草案

- `GET /runs/{run_id}/evaluation-sources`：列出该次运行实际记录、已审定且可评估的字段与对象归属。
- `POST /runs/{run_id}/evaluations`：提交 evaluation plan；服务端从 Run Bundle 构造 snapshot，运行只读评估并保存报告。
- `GET /runs/{run_id}/evaluations`：列出该运行的评估报告。
- `GET /evaluations/{report_id}`：读取报告。
- `POST /evaluations/{report_id}/configuration-draft`：创建新的配置草稿，不执行、不提交。
- 最终配置确认沿用现有任务版本/TaskSpec 校验和 run 创建流程，不另建第二套执行入口。

所有 API 必须复用现有身份认证、授权和审计中间件；本地开发模式也要限制 run_id 与 report_id 路径。

## UI 流程

1. 历史运行记录中增加“效能评估”入口。
2. 选择运行后，先显示该运行真实可用的评估参量，不展示未记录字段。
3. 选择评估方案/指标、时间窗口、采样约束和可选判据。
4. 生成独立评估报告，显示来源运行、数据覆盖、计算值、单位、判据、样本数和未评估原因。
5. 用户选择“根据结果调整配置”，进入新配置草稿；对每个建议项显示原值、建议值、依据指标和对应参数 Schema。
6. 用户确认后才调用现有配置校验；通过后才允许创建新运行。
7. 原运行记录、原始数据和原评估报告始终保留。

## 并行开发边界

- Track A（后端快照/报告）：只改新增 evaluation service、storage 和 API 路由，不改 UI。
- Track B（评估工作区 UI）：只改新增独立评估页面/模块与导航，不定义新的 API 数据格式；严格按本文 API schema。
- Track C（配置草稿适配）：只做只读映射与新草稿生成，不写原 TaskSpec，不触发 run。
- Track D（验收）：负责 API、持久化、跨运行隔离、数据真实性、权限和浏览器流程测试；不得为迁就实现放宽不变量。

并行集成顺序：先冻结 API schema 与错误语义 → A 实现并通过服务测试 → B/C 对接同一 schema → D 执行端到端回归。可并行开发，但不得各自发明相似 DTO。

## 必须测试的不变量

- 运行失败、取消、未验证或未封存数据不能作为正常评估快照。
- 客户端伪造 snapshot/run_id/source field/unit 不能绕过服务端 Run Bundle。
- 不同用户/工作区不能跨权限读取报告。
- 报告不能更改运行状态、原始遥测、TaskSpec 或参数。
- 缺失字段和不完整时间窗返回 NOT_EVALUATED。
- 不同评估方案可以评估同一 sealed snapshot，结果保留不同 plan digest。
- 重复提交相同 plan 幂等；不同 plan 生成不同报告。
- 配置草稿必须关联原 report/run，用户确认前不得提交或执行。
- 通过评估不代表运行本身通过；超出判据不代表运行失败。
- 效能评分不进入仿真对象树，安全校验仍保留在仿真执行链。

## 验收

- API 服务端真实读取归档遥测并生成 snapshot；
- 报告持久化、幂等、来源可追溯；
- API 权限和路径隔离测试通过；
- 浏览器完整完成“选择历史运行 → 评估 → 报告 → 新配置草稿 → 用户确认 → 原有校验”；
- 原有单次仿真、实时会话、Basilisk 原生和 Run Bundle 回归不退化。

## 不在 P9-B

- 自动优化算法、自动搜索参数、自动修改参数或自动重跑；
- 任务级效能权重与总分（需另行由用户/领域专家定义）；
- 三维地球主界面 Cesium 集成；
- 修改 Basilisk 物理模型。
