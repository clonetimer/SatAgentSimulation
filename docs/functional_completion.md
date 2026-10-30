> 以[当前逐项验收状态](../reports/current_requirements_status.md)为准。以下保留各修订的历史记录，其中“当前”“待完成”等措辞仅适用于对应修订，不代表最新状态。整体完成状态仍未证明。

## 2026-10-29 当前版本：资源损失幅度修复后重新验收

**整体尚未完成验收。** 当前物理与验证源码为 `84fa8bbd713f5b7aa80bd5095f658e70dedc042613feaa0448dcd79b63f10d82`。使用目的保持通用近地小卫星工程仿真，披露假设和误差边界；异常中止后保留证据、新建场景，不要求跨进程恢复同一原生状态。

uv 主环境已有实际离线安装、12/12 doctor、SPICE 原生执行及封存证据；独立 GPU 环境 51 个安装包与锁定版本逐项一致，uv 检查无依赖冲突。主应用 OSV 的 88 个依赖零漏洞记录不覆盖 GPU 环境。

上一物理版本 `0f8...` 在导出修复后完整 Windows 回归 **1845 通过、5 跳过、4 警告**；正式软实时八项、55 项原生浏览器工作流和 12 项异常留证/新建场景分别保留为该版本证据，不能自动作为新版本资格。

CSV 与独立遥测流修复已经应用：导出所选曲线及时间轴，按真实观测字段展开通配符，独立流从原始采样取值，未合成数据。真实 v5 模型的创建/修改 **3/3**、热环境脚本 **22 项检查通过**，实际脚本 CSV 已含全部五个请求观测量和正确采样时间。浏览器真实服务发现、完整模型探测和同一原生实例在探测期间继续推进通过，但随后在 390 像素弹窗边界检查失败，尚未完成全部网页流程。

弹窗宽度原先按视口取值，未扣除遮罩内边距；放大字体后高度也随全局 zoom 超出视口。已修复弹窗尺寸、主页面最小宽度及按实际可用空间收拢工作区。四种视口在 130% 字体下的弹窗、关闭按钮和键盘焦点检查通过，连同既有无障碍检查共 **7 项通过**；真实模型完整网页流程仍需重测。

更深入的原生物理实测发现旧事件解析丢弃顶层幅度：电池容量损失 0.1、0.3 都执行为默认 50% 损失。已修复三类资源损失的 `magnitude` / `severity` 到 `remaining_ratio` 传递，保留显式物理参数优先级。当前源码真实原生运行得到 **0.1 → 剩余 90%、0.3 → 剩余 70%**，显式损失参数对照也为 70%。37 项解析检查及原生事件迁移等合计 **60 项通过**。新物理源码因此重新进行完整回归、原生浏览器、异常重建和正式八项性能资格，目录为 `post-native-loss-magnitude-qualification-v6`，终态以前不宣布通过。

原始冻结输入的独立重放已完成：同一 v5 权重在旧精确合同下 **0/69、原始六类提示 0/6，整批 FAIL**，全部 69 份载荷除模型名之外保持原样，标签和响应未替换。新旧 69 条参考标签的物理模型、参数、时间和事件相同，但所有 `plots` 标签均不同；这解释了大量输出字段不一致，不能解释其余非法 JSON、缺少求解器配置和故障幅度错误。当前提示数据集 69/69、六类代表 6/6 的分数不得写成原始输入的同条件成绩。旧 v3 的 68/69 原失败仍保留；v5 没有提升为已验收模型服务。

此前四组各 7200 秒热工况在 `0f8...` 下完成：标称两步长及较高环境参数通过，较低参数情景太阳翼最低 199.948742 K，低于既有 200 K 下限，任务失败，未改阈值。119 项参数来源和工程假设已有实际场景证据。当前物理源码的热工况复测与模型/网页复验仍待完成；没有飞行标定、统一全工况精度上限或局地冷热极限保证。

耐久工具新增独立的完整应用源码与打包默认值绑定，覆盖导出、API、界面与资源文件；旧版 10 秒真实原生试验验证了校验链路。**同一实例真实连续至少 24 小时的当前版本正式耐久尚未启动或完成**，不能用加速模拟时间、多个片段或短时运行代替。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_scoped_credibility; phase=current_resource_loss_source_full_requalification; evidence=native_magnitude_and_export_repaired_formal_and_browser_pending; completion=unproven -->

<!-- Previous revision records below; latest status above takes precedence. -->

## 2026-10-29 当前需求验收状态

整体仍为 **未完成验收**。使用范围是通用近地小卫星工程仿真，披露参数来源、假设与误差边界；异常中止后保留证据并新建场景，不要求跨进程恢复同一物理状态。

uv 主环境已完成实际离线安装、12/12 doctor 检查和真实 SPICE 原生脚本执行及结果封存。主应用 OSV 审计覆盖 88 个依赖、零漏洞记录；该结论不覆盖独立 GPU 环境。

当前物理与验证源码指纹为 `0f8fb1d8a6df2ca647fbd81976349c60c8136279e497153fdbbefe6c6fa62b37`。最新 AI 结构约束改动后的完整 Windows 回归为 **1831 通过、5 跳过、4 警告**，含实际原生 180 秒连续仿真和真实 Chromium 交互，应用及测试源码前后一致。此前独立真实 HTTP/Chromium/原生浏览器工作流 **55/55**，异常留证及新建场景 **12/12**；AI 模型的完整实际网页流程仍待执行。跳过项及警告不视为通过项。

正式性能 **8/8 PASS**，测量前后物理源码一致：10 倍速实测 9.999568 倍；1 倍速漂移窗口实际 1800.000254 秒，P95 0.691605 ms、最大 88.4133 ms；14400 秒模拟时间长仿真实际用时 681.416823 秒。峰值合计 RSS 651624448 字节、峰值增长 490561536 字节，线程净增长 0。Windows 的 `fds` 不可测，`fd_growth=0` 是回退值，不能作为实际零句柄增长证据。**同一实例真实连续 24 小时的正式耐久记录尚未完成**，不能将多个片段或加速模拟时间换算替代。

四组当前源码、各 7200 秒 SPICE 热工况已经完成。标称 0.2/0.1 秒步长均通过任务边界，温度曲线最大步长差约 0.00995 K；较低全球均值 IR/albedo 工况太阳翼最低 199.948742 K，低于既有 200 K 下限，该工况任务 FAIL；较高工况通过。这是已披露设计与环境假设下的任务边界失败，不是运行崩溃。步长差和确定性参数敏感性不等于在轨物理误差上界或统计置信区间，不能改阈值制造通过。

独立复核已从原始 CSV 恢复正确的日食字段 `orbit.eclipse_factor`，原汇总中的 `-1` 是提取脚本误读缺失字段的占位值，原物理计算和轨迹不变；每组 146 个日食采样均满足直接太阳热为零、地球红外热保持正值。当前通用近地小卫星预设另经实际 600 秒原生执行、物理检查和结果封存，全部 **119 个**参数均有来源和工程假设记录，保持 analysis_only 和明确误差边界。

AI 使用真实 ModelScope 来源 `Qwen2.5-Coder-1.5B-Instruct` 与 LoRA。v3 原始扩展基准 **68/69，整批 FAIL**：模型把用户要求的电池容量损失幅度 0.3 生成为 1.0，事件检查拒绝执行，原始输入、输出及失败均保留。

v5 当前提示数据集完整 **69/69 PASS**，独立六类原始提示 **6/6 PASS**；但同轮生产创建/修改 **1/3**，热环境生成失败，候选整体未接受。新旧场景相同，但 24 条新请求补写默认幅度 1.0、提示包装也变化，不能把新分数写成原始冻结输入的同条件成绩。原始归档 69 份完整输入已核验哈希，独立重放仍待执行。

第一轮结构约束真实评测将生产创建/修改提升到 **3/3**，热环境仍因注册表 `array[number]` 未转换成 JSON Schema 而触发解码器内部错误、提前结束，原失败保留。已补齐复合类型转换、恢复分词约束中的数组预算，并拒绝异常提前 EOS；**45 项合同回归通过、真实分词器/解码器 3 项通过**。随后独立 `guided-production-evaluation-v5-compound-types` 真实复测达到 **创建/修改 3/3 PASS、热环境脚本 18 项 PASS**，包括实际脚本执行、原生物理校验、参数保留和结果封存，物理及 AI 工作流源码前后一致。未使用答案替换或模板回退。

原始冻结输入的完整 69 条重放及原始六类提示复测现已实际启动，目录为 `current-prompt-legacy-input-evaluation-v5`；仅更改模型名，原载荷没有 `response_format` 的事实也保留，因此该项测量原始解码能力，与生产流程的逐 token 约束分开报告。已返回用例出现额外默认 plots 与旧标签不一致、步长配置或事件幅度偏差，以及不合法 JSON；完整终态仍待结束，不能提前给通过结论。同期完整 Windows 回归已通过，不作为新的隔离性能资格测试。应用源码仍冻结至重放结束。

进一步核对导出制品发现 **CSV 完整性缺口**：最终任务与脚本选择了全部五个请求观测量，但 `dataset_writer` 仅按精确 `record_fields` 过滤，导出 CSV 只有 SOC 和载荷温度，漏掉轨道半径、两项地球热流及时间轴；封存的原始 telemetry.csv 保留全部字段。原 18 项报告未覆盖该检查，仍保留其原始分数，并另行记录 `guided-thermal-exported-dataset-completeness-audit.json` 的 FAIL。已在独立模块副本上验证合并所选曲线、保留时间轴及展开通配符的修复，7 项 I/O 检查通过，**生产源码尚未应用**；应待冻结重放完成后应用并重新执行真实脚本与完整回归。

GPU 环境通过 uv 增加 `lm-format-enforcer==0.11.3` 与 `interegular==0.3.3`，锁定包从历史 49 个变为 51 个；依赖相容性检查通过，不表示完成漏洞审计。候选评测结束恢复 v3 服务，不自动升版。

剩余工作：完成并审查原始冻结输入重放；应用并验收 CSV 完整性修复，保持原失败与基准分数；实际 ModelScope 网页发现、探测、生成、下载脚本及其全部请求字段执行、原生遥测和封存，以及模型调用期间的原生连续仿真和界面响应；数据导出改动后的回归；固定源码同一实例连续真实 24 小时耐久与独立审计。有限的视口和流程测试不能保证绝对无缺陷。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_scoped_credibility; phase=original_archive_replay_and_regression; evidence=guided_production_verified_original_inputs_pending; completion=unproven -->

<!-- Historical progress records below; latest status above takes precedence. -->

## 2026-10-28 当前源码正式性能通过，v5 生成评测开始

当前源码 `0f8fb1d8a6df2ca647fbd81976349c60c8136279e497153fdbbefe6c6fa62b37` 的完整 Windows 回归为 1815 通过、5 跳过、4 警告，包含真实原生 180 秒连续仿真。实际 HTTP/Chromium/原生浏览器 55 项通过，异常留证及新建场景 12 项通过。跳过和警告不是通过项，原始 XML 与日志保留。

正式性能已全部完成，8 项 PASS，formal_passed=true，测量前后源码一致。10 倍速实测 9.999568 倍；1 倍速漂移窗口实际 1800.000254 秒、18000 次观测，P95 0.691605 ms、最大 88.4133 ms，正常完成。14400 秒模拟时间长仿真实际用时 681.416823 秒，峰值合计 RSS 651624448 字节、峰值增长 490561536 字节，均低于既有预算；线程净增长 0。Windows fds 不可测，fd_growth=0 是平台回退值，不作为实际零句柄或描述符增长证明。本轮结果不等同同一实例真实 24 小时耐久、硬实时或硬件资格。

真实 v5 LoRA 已在 8032 候选端口加载，来源和数据哈希已核对，开始完整 69 条注册表增强验证。随后执行独立原始提示、生产创建/修改及热环境脚本。候选流程没有自动接受或升级；结束后恢复 v3 服务。v3 原始 68/69、整批 FAIL 保持不变。当前候选结果不能提前声明通过。

新旧验证集场景和时长一一对应，但 24 条新请求显式补写了默认幅度 1.0，生产提示包装也更新了。因此另行准备对原始归档 69 份完整载荷补测，仅替换模型名，并保留原始标签与执行检查。归档载荷已逐条通过旧报告 request_sha256 校验；补测未执行，需等当前评测及热工况复核结束。当前数据集的分数不会被写作原始冻结输入的同条件成绩。

当前队列 `post-training-validation-qa-finalization-v5/state.json` 处于模型评测阶段，独立状态为 `current-prompt-evaluation-stage-v5-finalization/state.json`。四组热工况复测等待当前队列退出及前置资格核验，避免并行负载。接受模型后的实际网页发现、探测、生成、下载脚本执行及原生结果封存仍待实测。独立 24 小时耐久仍待后续冻结版本完成。

uv 主环境的创建、doctor 和真实原生运行已留证。独立 GPU 环境 49 个包与锁文件一致、uv 依赖检查无冲突；主应用 OSV 88 个依赖零漏洞记录的结论不覆盖 GPU 环境。使用范围为通用近地小卫星工程仿真，披露可追溯假设和误差边界；不要求跨进程原生续算，不取得硬件或飞行标定资格。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_scoped_credibility; phase=v5_candidate_generation_evaluation; evidence=formal_performance_verified_model_pending; completion=unproven -->

## 2026-10-28 性能验收错误传播修复（覆盖下文历史状态）

当前源码为 `0f8fb1d8a6df2ca647fbd81976349c60c8136279e497153fdbbefe6c6fa62b37`。核对发现性能测量清理函数曾吞掉原生 `finalize()` 异常：注入收尾故障后虽执行 abort，却未向上报错。已修复为清理后重新抛出原始异常，防止测量成功掩盖收尾失败。专项 10 项通过，其中新增 5 项覆盖 quantum/长仿真收尾异常及二次清理异常；不改变物理算法、步长、温度阈值或性能预算。

此前源码 `055fc...` 的完整测试 1810 通过、5 跳过，真实原生浏览器 55 项及异常留证/重建 12 项通过，原报告全部保留。旧绑定正式性能已结束，8 项测量数值均通过，包括 10 倍速实际 9.998649 倍、1800 秒漂移 P95 0.7066 ms、4 会话峰值合计 RSS 约 1.36 GB；14400 秒模拟时间长仿真实际用时 731.221348 秒。最终来源检查因源码改变将整批判为 BLOCKED，formal_passed=false，未启动模型评测；不继承旧资格。Windows 的 fds=-1 表示不可测，报告中的 fd_growth=0 是平台回退值，不能宣称实际测得零句柄或描述符增长。

新队列为 `post-training-validation-qa-finalization-v5/state.json`，已在旧进程退出后自动开始当前完整 Windows 回归；随后执行实际原生浏览器、异常留证重建、全部正式性能及 v5 候选评测。四组热工况复测等待任务也已绑定新队列，不并行竞争测量资源。独立 24 小时耐久仍待冻结版本后完成。

uv 主环境已有创建与真实原生运行证据；GPU 环境 49 个包与独立锁文件一致、uv 依赖检查通过。v5 模型已训练完成但尚未接受，v3 的 68/69（1 条幅度错误、整批 FAIL）不改写。实际 ModelScope 网页生成、下载执行及结果封存仍待模型评测后验证。总体完成状态仍为未证明。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_scoped_credibility; phase=finalization_fix_queued_requalification; evidence=partial; completion=unproven -->

## 2026-10-28 当前版本浏览器与异常留证验收（覆盖下文历史状态）

当前源码为 `055fc17886f126821fdf1a95fdd05f3f60d8882f744754f34d51e779610b66c1`。完整 Windows 回归已完成：1810 通过、5 跳过、4 警告，1815 用例；包含实际原生 180 秒连续仿真。警告包括 FastAPI 依赖弃用、故意篡改 wheel 的重复文件测试，以及 joblib 查询 Windows 物理核心数时的系统命令缺失和输出解码线程警告；不把警告描述为零问题。

首次浏览器结果为 54/55，通过项包含真实物理运行与封存；唯一失败是新验收脚本从普通会话快照读取调度信息。已修正为连续会话状态接口，并加强检查，要求策略与封存结果元数据完全相同。运行代码及验证器未变，不重复运行同源完整测试。原失败报告保持。新一轮真实 HTTP/Chromium/原生浏览器 55/55 通过，四种屏宽、字体缩放和中文运行状态均覆盖。

Windows 实际原生异常留证与新建场景专项 12/12 通过：故障检测、子进程回收、不自动续算或重放、拒绝恢复旧会话、拒绝覆盖旧 ID、原始证据有效且保持不变、新场景从零开始、原生代次独立、SPICE 环境及正常封存。

当前队列为 `post-training-validation-qa-native-scheduling-v4-browser-fix/state.json`，已进入正式性能。前一修订的 7/8 正式性能和 10 倍速实测 7.261736 倍失败记录保留；调度修复的短测 9.974895 倍不替代当前正式结果。v5 训练完成、尚未接受；完整模型基准及实际模型网页流程待完成。热工况四组复核已设置等待任务，不与当前验收并行。真实同一实例 24 小时耐久尚未完成。

uv 新建环境与真实 SPICE Run Bundle 证据已完成。独立 OSV 对 88 个锁定依赖审计零漏洞、无跳过；旧 PyPI 辅助查询已取消，其 ERROR 不作为审计通过。v3 原始基准为 68 条通过、1 条失败、整批 FAIL，不改写。通用近地小卫星工程范围、可追溯假设与温度边界保留，不要求跨进程恢复原生状态。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_scoped_credibility; phase=current_source_formal_performance; evidence=partial; completion=unproven -->

## 2026-10-28 调度修复后重新验收（覆盖下文历史状态）

当前源码为 `055fc17886f126821fdf1a95fdd05f3f60d8882f744754f34d51e779610b66c1`。新版队列 `post-training-validation-qa-native-scheduling-v4/state.json` 已启动完整 Windows 测试，并核验运行进程 PID 与创建时间；随后执行 55 项实际原生浏览器、12 项异常留证与重建场景、全部正式性能及 v5 评测。当前不宣布整体完成。

前一修订全套测试为 1803 通过、5 跳过，实际原生浏览器 50 项通过；正式性能终态为 7/8 通过，10 倍速实际 7.261736 倍导致倍率项失败。30 分钟漂移、并发容量和 4 小时模拟时间检查均已完成，不再处于测量中，也不等同真实 24 小时耐久。

混合核心 B-A-B-A 对照中，系统调度中位数 7.772492 倍，性能核心中位数 11.158010 倍，完整遥测相同。生产子进程增加可配置、可追溯的性能核心偏好；短测实际 9.974895 倍，但不是完整正式资格。最终专项 15 通过、3 跳过。详见 `docs/native_runtime_scheduling.md`。

旧 PyPI 辅助查询已按身份核验取消，不需恢复；其 ERROR 不作为零漏洞结论。独立 OSV 88 个锁定依赖零漏洞、无跳过的证据保留。v5 已训练完成、尚未接受；v3 基准为 68 条通过、1 条失败、整批 FAIL，原报告不覆盖。独立 24 小时长测及当前模型实际网页链路仍待完成。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_scoped_credibility; phase=current_source_full_regression; evidence=partial; completion=unproven -->

## 2026-10-28 依赖修复与新一轮资格验证（覆盖下文历史状态）

主应用锁定依赖已更新为 certifi 2026.10.19、filelock 4.2.1、msgpack 1.2.3、Pillow 13.0.0、setuptools 84.1.0，并实际通过 uv 安装；`pip check` 无冲突。原始 45 条漏洞记录保留在 `dependency-audit-20261028-vulnerabilities-original/`。OSV 对更新后的 88 个锁定依赖全部执行审计，无跳过项、无漏洞记录，实际退出码 0；`dependency-security-upgraded-gate.json` 对照当前依赖集合通过，零发现。这是该时点主应用锁文件的审计结果，不能外推为不存在任何安全问题。另一次 PyPI 复扫的网络失败报告保留，后续 PyPI 查询尚无终态；不把查询失败或未结束当作零漏洞。

为兼容 Pillow 13，新增独立 `bsk==2.11.0+satfix2` 发行元数据变体。构建使用固定官方 Windows wheel SHA-256，1149 个 Basilisk 载荷文件逐字节不变，新 wheel SHA-256 为 `881f39463708c975c6aac2b9cfb2348d1a33eece0e6390847ef93d1710badc4e`；来源、METADATA 与 RECORD 校验通过。旧 satfix1 wheel、构建报告和离线包清单均保留。uv 安装入口现在把已核验 wheel 的明确路径交给解析器；当前安装流程不依赖从同版本候选中任意选择。此变体不是实验性 C++ checkpoint wheel，不增加原生断点续算能力。

已用 uv 从缓存离线创建全新的 90 包环境 `uv-offline-security-env/`。实际环境检查 12/12 通过，`pip check` 无冲突；真实 SPICE 整星 Run Bundle 执行、验证和封存通过，证据为 `uv-offline-security-native-file-entry/report.json`。首次辅助命令从 stdin 调用 Windows multiprocessing，子进程无法加载 `<stdin>`；该失败保留，核对 PID/创建时间且确认无原生子进程后只终止其诊断父进程，随后由带 main 保护的独立文件完成验证。原失败 Run Bundle 不改写。`security-uv-setup-offline-exit.json` 另记录修改后的完整 uv 离线安装入口实际退出码 0。

本轮专项：来源与依赖测试 16 通过；扩展运行时/治理测试 38 通过；实际原生、导出脚本及热容量回归 21 通过；完整单次创建界面回归 4 通过；发布与审计一致性检查 7 通过。这些集合有重叠，不能相加为唯一测试数。曲线拖拽测试补齐手工坐标操作前的滚动定位，仍保留可见选区与真实采样缩放断言。

新资格队列 `post-training-validation-qa-security-v3/state.json` 已完成全套 Windows 测试：1803 通过、5 跳过、零失败，用时约 26 分钟；随后真实原生浏览器 50 项检查通过。当前执行新版正式性能，倍率检查中 0.1～5 倍速通过，10 倍速实测 7.261736 倍、未达现有 0.85 目标达成率门槛。30 分钟 1 倍速漂移仍在测量，保留全部终态后定位性能回退，不调整判据。此次绑定为 `b10e820acbfe9f417407970e8709cec40661b7a2aa2af12837e2b9a4380e55a5`；旧的 1768 通过、30 失败、4 跳过报告保持原样。正式性能未全项通过时，不启动 v5 候选评测。独立 24 小时长测尚未启动；v5 已训练完成但尚未接受，原 v3 的 68/69 FAIL 不改写，也不自动切换模型。当前简明逐项判定见 `reports/current_requirements_status.md`。

来源记录为 `security-revision-v3-provenance-with-asset-policy.json`。目标仍为通用近地小卫星工程仿真，披露假设与误差边界；异常留证后新建场景，不要求跨进程原生续算。Linux/vLLM 历史资格与反作用轮专家审批不继承。下文“漏洞尚未修复”“当前源为 fba82… ”等描述是前一阶段记录，不覆盖本节。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_credibility; phase=full_suite_then_requalification; evidence=partial; open=full_suite,formal_performance,v5_generation,actual_model_browser,24h_endurance; completion=unproven -->

## 2026-10-28 全套测试失败后的修复进展（覆盖下文历史状态）

v5 LoRA 已完成 222 步、3 个 epoch 的实际训练，状态为 TRAINED_EVALUATION_PENDING；尚无完整生成评测成绩，原 v3 的 68/69 FAIL 不改写。训练后第一次全套 Windows 测试为 1768 通过、30 失败、4 跳过，原报告 `post-training-validation-qa/whole-suite.xml` 保留。此后没有新全套通过结论，也没有有效的当前源码正式性能或 24 小时耐久结果。

已修复实时配置页缺失的 4 个热环境字段、Windows 浏览器路径选择、缓存/临时产物误计为项目脚本、若干测试中不对齐的采样配置，以及反作用轮单机请求被误路由到姿控分系统的问题。Windows 进程试验证明虚拟环境启动器 PID 与实际解释器 PID 不同；监督器现在直接持有实际解释器进程，保留虚拟环境依赖，仍要求 READY 与所持进程完全匹配，不以子孙 PID 替代死亡证明。真实原生子进程终止与固定试验图对照回归通过，不构成生产跨进程续算支持。

当前专项结果：`qa-process-propulsion-v4.xml` 26 通过；`qa-thermal-governance.xml` 11 通过；`qa-evidence-policy.xml` 15 通过、1 跳过；`qa-native-soak-valid-grid.xml` 真实 180 秒仿真推进通过，实际用时约 24 秒；曲线拖拽孤立复测通过，但此前整套中的失败仍保留，尚不能判为已消除偶发问题。各组包含重复用例，不能相加为唯一测试数。初次专项的 89 通过、4 失败和后续路由专项失败均保留。

热控 A/B 保留原 339 K、200 W 近保护阈值工况。实际记录证明退化使峰值温度上升，同时改变载荷保护断电时序，因此某个后期采样温度可以较低；测试同时检查散热功率降低、峰值升高与实际负载反馈，不调整 340 K 阈值。原始完整对照见 `qa-thermal-feedback-diagnostic/`。320 K 探针中散热器尚未展开，未被用作退化通过证据。

已离线重新核验 7 个必需环境资产，并由实际资产摘要生成恢复校验器所需清单；当前 Basilisk 运行时通过。Linux 离线 wheel 缺失仍列为可选资产未提供；Linux/vLLM 资格和历史反作用轮专家审批仍为 NOT_REVALIDATED。测试只验证未复验时不得继承资格；它们不代表完成 Linux 验收或新增专家批准。实验性定制电池 checkpoint wheel 在普通环境中缺失时明确跳过；专用 CI 强制要求该 wheel，缺失仍失败。

新增阻碍：实际 pip-audit 返回 45 条漏洞记录，涉及 certifi、filelock、msgpack、Pillow、setuptools。安全门禁 `qa-dependency-security-gate.json` 拒绝继承通过。已修复审计缓存写到工作区外的问题；漏洞尚未修复，不以“扫描运行成功”声明安全通过。Pillow 修复涉及当前 Basilisk 兼容 wheel 的依赖上限，需验证新的发行元数据及运行兼容性后更新环境，再冻结版本重跑全套、正式性能与模型评测，最后开展独立 24 小时长测。

机器可读进展为 `qa-repair-progress.json`；本次源码绑定为 `fba82ca82f076096ddde69ab3b794ece5bb22f9f41f9b846292647ee39c2d5f0`。下文涉及“正在训练”“待执行 180 秒测试”等描述均为历史记录，不覆盖本节。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_credibility; phase=repair_and_requalification; evidence=partial; open=dependency_vulnerabilities,full_suite,formal_performance,v5_generation,actual_model_browser,24h_endurance; completion=unproven -->

## 2026-10-27 电热账修订：当前判定（覆盖下文历史状态）

项目尚未整体通过验收。uv 环境及独立离线安装已实测；离线/实时、异常留证后重建场景、ModelScope 基座 LoRA 服务和前后端主要流程已有分项证据，不能据此声称全部工况真实或界面“无可挑剔”。

2026-10-28 最新状态：统一验证器补齐 acceptance_assertions 指标断言判定，修复另一处假成功；比较器纳入 runtime-and-validation-v5 源码绑定。当前绑定为 `655223aeb5ef199536eb45a5099a14c276fbeb0b6fe16f4124506c2b59c1dc93`。物理模块未改，但验证器与绑定范围已变，旧正式性能资格不继承。训练保持原任务运行；旧评测等待进程已在推理开始前按 PID/创建时间核对后停止并留证，新等待进程负责训练后依次运行全套测试、原生浏览器回归、新版正式性能，全部通过才执行 v2 候选模型评测。状态见 `post-training-validation-qa/state.json`。

本轮修复原生整星热网络的重复常量热源，以及未计入电池的恒温加热器耗电。真实原生 A/B 对照确认 12 W 电耗对应 11.4 W 入热；18 W 总线用电按 2/8/3/5 W 分配，载荷不再额外叠加 25 W。`thermal-energy-accounting-explicit-load.xml` 30 项通过，含真实离线/实时热越限失败留证和封存拒绝。此前测试失败结果均保留；旧越限工况因修复而成为正常工况，现用显式 200 W 载荷触发真实越限，阈值不变。

当前修订的四组 7200 秒整圈已完成，源码前后一致：名义 0.2/0.1 秒步长及较高红外/反照情景任务通过，较低情景任务失败，太阳翼最低 199.948742 K，低于 200 K 下限。证据为 `thermal-energy-corrected-full-cycle/report.json`。两步长最大采样温差 0.00994673 K，这是数值差而非真实硬件误差保证。旧四组整圈数据仅作为历史结果保留。旧长测因源码变更已留证终止，当前没有有效的 24 小时长测，不累计旧时长或继承旧 PASS。

AI 当前服务为 ModelScope 来源的 Qwen2.5-Coder-1.5B + v3 LoRA；修正辅助脚本 Windows main 保护后，实际热环境请求端到端 18 项检查通过，含实际模型调用、参数保留、生成脚本执行、物理检查与封存，源码一致；证据为 `thermal-energy-corrected-real-model-product/report.json`。扩展基准 68/69 仍整体 FAIL。用户显式输出字段的确定性绑定与模型原始回答准确率区分，原始响应及此前失败保留。

当前源码的真实 HTTP/Chromium 原生流程 50 项通过，覆盖四种屏宽和字体缩放（`native-browser-energy-accounting-chromium/report.json`）；原生进程异常后留证并新建场景 12 项通过（`native-failure-recreation-energy-accounting/report.json`）。当前 uv 环境 doctor 12 项通过，pip check 无冲突。首次浏览器复测因未指定 Chromium 路径而未启动，该失败记录保留，随后指定实际已安装浏览器完成复测。

证据均位于 `reports/local_requirements_completion/`。下文为此前各版本记录，涉及“最新/当前/运行中”的描述不能覆盖本节。

前一验证器修订的正式性能 `spice-performance-energy-accounting-formal.json` 已完成，八项全部 PASS，前后源码指纹均为 `75f6f2d83ae576cd5b9e51a6d3c4f660b49a80baec8f4f4ea6fd26b2fe14fad0`。实际墙钟漂移窗口 1800.000282 秒、18000 次观测，P95 为 0.71631 ms，最大 99.8736 ms；4 小时仿真完成 14400 次推进，终态线程减少 2、句柄增长 0、总 RSS 减少约 3.27 MB，尾段 RSS 增长为 0，父子进程合计峰值约 653 MB。10 倍速正式窗口实测 9.999476 倍。该报告保持历史真实性；当前验证器修订后的正式资格待新运行，不继承旧 PASS。此前 CI 报告 BLOCKED 也保持原样。

正式测试完成后，后续脚本构建了 `taskspec_lora_v5_current_prompt_event_aligned` 数据：保留 588/69 条记录及全部场景和分区，使用当前生产提示词；所有样本通过实际基座分词器检查，最长 11045 token，未截断。新一轮 LoRA 已实际开始，输出为 `training_runs/qwen25_coder15_taskspec_lora_v5_current_prompt_event_aligned/`，当前训练来源状态为 TRAINING。为释放 GPU，原 v3 服务已在核对 PID、创建时间和完整命令后停止；驱动脚本在训练退出后恢复原 v3 服务，新模型不自动替换服务或基准成绩。此阶段尚不构成模型准确率提升或发布证明，旧 v3 适配器和原失败基准保持。

训练后评测原等待队列 `current-prompt-evaluation-stage/` 已因验证器源码变化而在推理前停止，`external_termination.json` 保留身份及终止原因，未影响训练。新版等待队列为 `post-training-validation-qa/state.json`；它先验证全套测试、实际原生连续回归、新版正式性能，合格后才启动 `evaluate_after_current_prompt_training_v2.py`。候选仍在 8032 端口分别执行完整 69 条注册表增强验证、原始提示词场景族验证、生产创建/修改链和热环境脚本执行，评测后恢复 v3，不自动成为正式服务。旧 15 文件模型快照保留，新验证器、比较器及调度源码见 `validation-revision-v5-provenance.json`。

补充修复 Windows 测试收集问题：`tests/interactive/test_continuous_soak.py` 不再在导入时依赖 Linux `resource` 与 `/proc`，Windows 使用真实 `peak_wset`、当前 RSS 使用 psutil，Linux 保留 ru_maxrss；原 1 GiB 增长门槛不变。Windows 收集及实际内存指标探测通过（`windows-continuous-soak-collection-probe.json`），连续模式跨越单次会话上限的控制逻辑回归通过（`continuous-mode-limit-semantics.xml`）。完整 180 秒原生测试已记录在待验清单，尚未执行；以上不作为物理耐久或 24 小时证据。生产物理源码指纹保持正式性能通过版本不变。

完整 Windows 测试集收集检查已结束：1781 项、零收集错误，状态为 COLLECTED_NOT_EXECUTED（`windows-test-collection-after-portability-fix.json`）。这不是 1781 项执行通过；完整执行已列入训练和模型评测后的待验清单。

2026-10-28 补齐 ModelScope 网页配置与生产验收的默认传输契约：保存服务现在使用 300 秒、4096 token、json_schema，保留显式自定义值。`modelscope-web-profile-alignment.xml` 的 5 项 HTTP/协议回归通过；它们不是实际模型生成成绩。网页/API 补充源码快照为 `modelscope-web-integration-provenance.json`。实际模型的浏览器发现、保存测试、生成、执行与封存流程已加入候选评测后的待验清单，不能用命令行中显式指定报告目录配置的成功替代该网页流程。

2026-10-28 修复导出 Python 的假成功：原脚本只检查摘要 status，且组件 validation 声明会被裁剪，导致实际任务失败或缺少必需证据时仍返回 0。修复前的 `script-validation-before.xml` 为 3 失败、1 通过；首次接入验证器后仍发现声明丢失的两个失败，原记录均保留。现在两种 Python 导出格式保留 validation，并复用生产运行验证器，输出 validation_outcome，FAIL/INCONCLUSIVE 均非零退出，正常任务仍成功。`script-validation-regression.xml` 21 项通过，覆盖两种导出、全部活动能力默认编译导出及既有执行验证。真实原生热越限的前后 trace.csv 与运行标签逐字节一致，修复只改变验证与退出语义，不调整物理参数或安全阈值（`exported-script-validation-closure.json`）。

真实模型浏览器验收脚本已落地为 `run_actual_modelscope_browser.py`：要求核对实际模型与训练来源，走真实页面发现/保存/测试、自然语言生成、图形流程导出与下载 Python、实际执行下载文件，再通过页面运行并校验原生遥测和封存；含四种屏宽的设置、代码和结果布局检查。当前仅完成语法、入口和 CLI 检查，未运行浏览器，不计为模型或前端验收通过。

指标断言闭环证据：`metric-assertion-before.xml` 保留 8 个失败复现；`acceptance-assertion-closed.xml` 30 项回归通过，含真实生产 Run Bundle、两种脚本格式、必需缺失、可选未评估及无效配置。`validation-v5-binding.xml` 17 项通过，验证修改/缺失比较器会使源码资格失效，并保留原生离线/实时任务失败的判定。各组包含重复用例，不累加成唯一测试数。新版正式性能与独立耐久仍待完成。


# 功能补齐与验收记录

目标保持为：完善的单次离线/实时仿真、通用近地小卫星工程模型、AI 辅助生成可执行脚本、可靠的前后端交互和可视化。用户于 2026-10-26 明确第（2）项按通用近地小卫星工程仿真验收，披露可追溯假设和误差边界。此文件记录进展，不作为验收通过证明。

## 2026-10-27 当前验收状态

整圈补测终态：`thermal-environment-full-cycle/report.json` 为EVIDENCE_CAPTURED，四组7200秒原生任务约束均PASS且来源一致；步长采样温度最大差0.009947K，求积细化反照/红外最大差分别0.041952/0.023765W/m²。该结论只覆盖声明的均匀地球与等效表面模型。`native-routing-closed-regression-final.xml` 79项通过；旧测试中要求模型超时后生成模板、或不支持FEM时生成组件模板的预期，已改为验证禁止替代、拒绝编译并保留结构化拒绝计划，未放宽生成或执行门槛。新版AI、性能与24h终态仍分别待完成。

最新补充：有限地球热环境两步长7200秒及较低参数情景通过，采样温度最大步长差0.009947K；较低情景太阳翼最低仅200.0178K，安全余量不足以宣称稳健。`thermal-native-realtime-smoke/progress.json` 已封存，`native-failure-recreation-finite-earth/report.json` 12项通过，`native-browser-finite-earth-disclosure-verified/report.json` 50项通过。新版正式性能与24h分别读取 `spice-performance-finite-earth-thermal.progress.jsonl`、`endurance-24h-finite-earth-thermal/progress.json`；10倍速已测只有约7.44倍，保留失败。AI实际新请求因SPICE规划误拒绝的缺口已修复并通过28项针对性回归，随后实际模型请求300秒超时；失败记录在 `finite-earth-real-model-product*/` 中，2048 token独立复测在执行，不能声明新请求已经生成成功。下文更早快照中的“最新”和旧路径不覆盖本段状态。

17:00更新：原生整星热网络已接入有限球形地球环境，分别记录直射太阳、反照、红外热输入及太阳翼实际电能扣除。机身节点使用明确披露的六面等效暴露面积；太阳翼使用实际EPS法线和双面面积，光学参数可在单次表单与实时初始配置中设置。原始几何核22项通过；三种相位下与独立官方Basilisk反照模块误差小于1%，离线/分段执行一致。此数值对照不构成真实卫星温度误差上限。整圈与敏感性验证尚在进行，之前的正式性能及24h长测不继承为新物理版本资格。

界面最新证据 `native-browser-responsive-accessible-final/report.json` 为47项通过，覆盖实际HTTP/Chromium原生流程、四种屏幕宽度与四档字体；43项相关前端回归和3项针对性移动端检查另行记录，计数相互重叠、不累加。手机使用折叠工具与模板库、当前状态优先、同一组固定底部会话按钮和焦点恢复。本文以下的20/23项浏览器结果为各自较早版本记录。

实时任务结果的两个漏检已修复：封存现在读取原生运行摘要，显式任务失败或不确定不会因技术执行完成而变为通过；原生记录器分段清理前，以常量内存累计全程能源、存储和热安全约束。共享物理检查同时读取模型声明的 `thermal.safe` 与各节点安全标志，早期越限即使随后恢复也保留失败。拒绝通过封存时，工作区仍保留 `validation_outcome.json` 和原始运行结果。

最新回归 `mission-history-verified.xml` 为 **92 项通过**，包括真实有限时长/连续原生热越限负例、恢复后历史越限、缺失证据、分段一致性、封存与生成脚本验证。真实异常中止后新建场景 `native-failure-recreation-mission-history/report.json` **12 项通过**；真实 HTTP/Chromium 工作流 `native-browser-mission-history/report.json` **20 项通过**。这些检查相互重叠，不累计成唯一用例总数。

`endurance-mission-history-final-smoke/progress.json` 的 15 秒短测通过、源码及运行器前后指纹一致，但不等于 24 小时验收。最新独立长测为 **`endurance-24h-mission-final/progress.json`**，正式性能复测为 `spice-performance-formal-mission-history.json`。前面的 `endurance-24h-aligned`、`endurance-24h-coverage` 和 `endurance-24h-mission-history` 均属于先前代码或运行器版本，只保留原始证据，不继承为当前版本通过时长。连续运行审计与任务约束分别记录；即使运行审计满足时长，也不会覆盖热约束失败。

AI 的原始提示留出 **5/6**、注册表增强提示 **6/6**、此前真实生产创建/修改 **3/3** 各自保留。全部 69 条注册表增强验证已结束，**68/69 通过，整批状态 FAIL**；唯一失败将用户要求的电池容量损失幅度 `0.3` 生成为 `1.0`，事件对比拦截后未执行该脚本。证据为 `qwen25-lora-v3-all-validation-registry/report.json` 及对应原始请求/响应。这是有限合成合同数据上的结果，不能外推为任意自然语言请求的准确率。JSON 提示契约修复后的生产复测仍以终态报告为准。

JSON 传输提示及响应 Schema 已与现有旧版、规范版和包装格式对齐。`json-draft-envelope-verified.xml` **34 项通过**，包括 43 个注册能力示例的格式检查以及格式合法但仿真参数非法仍被拒绝的负例。最新真实模型生产复测 `qwen25-lora-v3-product-envelope-aligned/report.json` **3/3 通过**；本次创建与整星生成无需格式重试，修改流程仍因模型输出未声明字段触发一次重试。未使用模板或参考标签替换模型响应，不能声明完全消除了重试。

结果概览隐藏内部版本和全程累计实现字段，优先显示实际指向误差、荷电状态及存储数据量；数据存储使用 `bit`，指向误差使用 `deg`，SOC 使用 `ratio`。存储数据量不再被误译为储能。

最新界面与单位修复后的真实 HTTP/Chromium 流程 `native-browser-overview-units-final/report.json` **23 项通过**，包含数据量标签和单位、内部版本隐藏、离线运行及实时暂停/步进/封存。截图保存在同目录。

补充回归修复了明确请求 EPS 分系统、但输出中出现电池 SOC 时被误选成电池单机的问题。`overview-units-routing-closed.xml` **36 项通过**；`json-envelope-routing-verified.xml` **38 项通过**，包含单机、基本 EPS、统一原生 EPS 和明确源码能力选择的反例，原有验收断言保持。

`endurance-24h-readonly-mission-history/report.json` 使用最新热安全检查对旧原始轨迹进行只读复核，完整校验 288102 条链记录、144051 帧原生轨迹，得到 **1078 条失败、101172 条警告**；详细样本仍限制为 2048 条，准确计数未截断，采样峰值 RSS 48168960 bytes。旧摘要曾报告任务 PASS，新检查保留早期热安全失败。原始进度和遥测清单哈希保持，不将只读复核冒充新物理试验或24小时通过。

## 后续补充与训练数据

2026-10-27 补充：`spice-performance-formal-mission-history.json` 正式复测 **8/8 PASS**，30分钟实际墙钟漂移 P95 13ms、最大16ms；4小时仿真时间检查通过，源码前后指纹一致。4小时仿真时间不等于4小时墙钟耐久。

生产生成主路径已增加明确事件 JSON 的一致性门禁。`event-production-contract-final.xml` **75 项通过**；`event-contract-product/report.json` 记录旧错误模型响应在编译前被拒绝，重新调用真实模型后正确生成同一用户要求并通过实际脚本与运行验证。模型回答和原始68/69评分未改写；该独立生产专项不计入原始基准成绩。无结构化事件声明的自然语言仍需预览确认，不声称能够判定任意自然语言意图。

新门禁对旧冻结数据复核为633/645匹配，其余12条为历史事件投递字段与运行标签不一致。旧模型和数据不修改。新建的 `data/training/taskspec_lora_v4_event_contract_aligned/` 为 **588条训练、69条验证**，生成时验证事件一致性、参数、时间配置及编译；训练加载器也拒绝重新计算哈希后仍不一致的事件。原645条场景与时长全部保留，新增12条通用近地工程场景，详见 `sft-v4-event-contract-evidence.json`。该新数据版本**尚未训练，不代表模型准确率提高**。较早的 `taskspec_lora_v4_event_contract/` 为中间诊断产物，不用于训练。构建器现在要求全新输出目录，防止覆盖已用于训练的证据。

2026-10-27 补充：`spice-performance-formal-mission-history.json` 正式复测 **8/8 PASS**，30分钟实际墙钟漂移 P95 13ms、最大16ms；4小时仿真时间检查通过，源码前后指纹一致。4小时仿真时间不等于4小时墙钟耐久。

生产生成主路径已增加明确事件 JSON 的一致性门禁。`event-production-contract-final.xml` **75 项通过**；`event-contract-product/report.json` 记录旧错误模型响应在编译前被拒绝，重新调用真实模型后正确生成同一用户要求并通过实际脚本与运行验证。模型回答和原始68/69评分未改写；该独立生产专项不计入原始基准成绩。无结构化事件声明的自然语言仍需预览确认，不声称能够判定任意自然语言意图。

新门禁对旧冻结数据复核为633/645匹配，其余12条为历史事件投递字段与运行标签不一致。旧模型和数据不修改。新建的 `data/training/taskspec_lora_v4_event_contract_aligned/` 为 **588条训练、69条验证**，生成时验证事件一致性、参数、时间配置及编译；训练加载器也拒绝重新计算哈希后仍不一致的事件。原645条场景与时长全部保留，新增12条通用近地工程场景，详见 `sft-v4-event-contract-evidence.json`。该新数据版本**尚未训练，不代表模型准确率提高**。较早的 `taskspec_lora_v4_event_contract/` 为中间诊断产物，不用于训练。构建器现在要求全新输出目录，防止覆盖已用于训练的证据。

## 安装和操作

从项目根目录运行 `python scripts/setup_uv.py`。脚本使用 uv 创建 `.venv`，安装固定依赖，核验官方 Basilisk 发行载荷，并预取环境资产。`--offline` 只使用现有文件和 uv 缓存，缺失时失败；`--without-environment` 只安装开发环境。

Windows 启动：激活 `.venv`，设置 `$env:SAT_SIM_INTERACTIVE_ENABLED='1'` 后运行 `scripts/start_local_windows.ps1`。单次任务和实时会话分别保留独立运行记录。

轨道环境选择：

- `simplified`：历史参考工况，固定太阳、地球点质量、中心偶极场，不能等同真实任务环境。
- `spice_wmm`：本地 DE430 星历、地球 J2、日月引力、WMM2025；显式 UTC 历元。缺数据即拒绝执行，不回退。WMM2025 仅用于 2025—2029 年。CSPICE 内核池是进程全局资源，同进程仅允许一个该环境实例；并发任务使用独立 worker 进程。

环境参考数据只说明物理环境来源，不能提升卫星硬件参数的标定等级。

## 验收清单

| 项目 | 当前状态 | 必须检查的证据 |
|---|---|---|
| uv 可重复安装、依赖一致性 | 当前环境及 uv 离线新环境安装、pip check 与严格 doctor 通过 | doctor.json、offline-doctor.json（12/12 PASS）；offline-install.log |
| Windows SQLite 连接释放 | 已修复，事务与并发回归通过 | sqlite/experience 回归；无 GC 的句柄关闭与回滚 |
| 前后端版本与功能开关一致 | 通过当前回归 | 实际 HTML/静态资源/health、关闭实时功能无 404 |
| 桌面/平板/手机布局 | 四种宽度、键盘交互与真实 HTTP 原生工作流通过；持续验收 | 1920/1366/768/390 无横向溢出；ui-accessibility.xml；native-browser-sampling/report.json |
| 完整轨道几何与 UTC 环境 | 原生环境与数值回归通过 | orbital-tests.xml；真实星历/WMM、三组积分步长收敛 |
| 单次/分段/实时执行一致性 | simplified 与 spice_wmm 原生对比通过 | native-parity.xml；单次与同一原生实例分段推进的各遥测数值对比 |
| 软实时正式性能 | 最新全程任务校验下正式复测8项通过 | spice-performance-formal-mission-history.json；30分钟实际墙钟P95漂移13ms，源码前后指纹一致 |
| 持续运行与中断恢复 | 异常中止保留证据并新建场景12项通过；当前独立24h试验进行中 | native-failure-recreation-mission-history/report.json；endurance-24h-mission-final/progress.json；旧版本时长不继承 |
| 真实模型生成脚本 | Qwen2.5-Coder-1.5B完成216步微调；最新生产3/3、独立事件专项通过；扩展68/69整批FAIL保留 | qwen25-lora-v3-product-envelope-aligned、event-contract-product、qwen25-lora-v3-all-validation-registry；不以标签替换模型回答 |
| 通用近地小卫星工程真实性 | 按用户明确范围披露工程假设、模型适用域与数值/外部参考残差 | 参数来源、工况、误差边界；不要求指定卫星飞行标定，也不作该等级声明 |

## 数据与验收边界

用户于 2026-10-26 明确实时异常恢复范围为“异常中止后保留证据，重新创建场景即可”。本轮不要求同一物理状态的跨进程续算；暂停后继续仍仅适用于存活的原生实例，失效实例的恢复与隐式指令重放拒绝门禁继续保留。`scripts/run_native_failure_recreation.py` 对新建测试场景的自有原生子进程注入中止，验证失败检测、子进程回收、旧场景恢复/覆盖拒绝、原始证据哈希与归档、新场景从零运行并封存，以及旧证据未被改写。`native-failure-recreation/report.json` 的 12 项检查通过；该结果不代表断点续算，也不替代持续 24 小时运行验收。

推荐模板 `generic_leo_small_sat_engineering` 提供 SPICE/WMM 通用近地小卫星工程场景。模板的适用域、未建模效应与误差声明随表单编辑进入运行记录；115 个输入参数均披露来源或假设，提交表单本身不等于独立硬件标定。600 秒原生工程场景的 9 项执行检查通过（`generic-leo-engineering-case-file-entry/report.json`）。参数来源、模板、原生运行、蒙特卡洛与界面合并回归 51 项通过（`generic-final-validation.xml`）；真实 HTTP/Chromium 工作流 18 项通过（`native-browser-generic-preset/report.json`）。这些是已测场景的结果，不构成所有配置下的精度或界面完美性保证。

用户已确认采用通用近地小卫星工程仿真，披露可追溯假设与误差边界；指定卫星硬件/飞行标定不再作为本轮完成的前置条件。用户确认暂无模型服务，允许基于 ModelScope 基座微调。仍不得用合成数据、协议桩或模板伪造真实模型调用，也不得用短测替代正式时长门禁。

## ModelScope LoRA 路线

冻结 v3 的六个留出参考标签还经过独立生产执行复核（不是模型输出）：初次结果为 5/6，反作用轮卡死场景的数据守恒审计未计入原生整数 bit 舍入。按 Basilisk 2.11.0 官方 `SimpleStorageUnit::integrateDataStatus` 的逐输入、逐步舍入实现，加入可推导的 `0.5 bit × 已连接输入数 × 实际物理步数` 上界，并分别保留时序、整数舍入和浮点容差。原始残差仍为 -1000002.0047779009 bit，未修写为零；该场景的时序预算 1000000 bit、舍入上界 450 bit、浮点预算 1 bit。原始失败报告保留于 `sft-v3-reference-execution/report.json`；同一冻结数据复测 6/6 通过，见 `sft-v3-reference-execution-rounding/report.json`。原生分数 bit 输入、容量截断反例及执行验收 6 项通过（`storage-rounding.xml`），复合场景与导出等回归 27 项通过（`composite-rounding.xml`）。该边界不覆盖容量溢出或缺失的数据，不构成 AI 生成成功率。

运行制品完整性校验补上 `SEALED.json` 对 manifest 哈希及运行标识的绑定，拒绝无封存记录、损坏封存记录以及只修改制品并重写 manifest 的情况。相关负例、真实任务执行与实时封存/恢复回归 24 项通过（`seal-binding.xml`）。这是本地一致性校验，不是密码学签名或对共同改写所有证据的独立认证。

后续复核进一步拒绝空清单、非法条目、目录和不规范路径；先确认解析路径仍在包内，再读取并核验文件，读取失败返回结构化完整性问题。兼容历史仅 SHA 字段的条目；声明大小时必须匹配。扩大后的制品负例、真实执行及实时封存/恢复回归共 49 项通过（`seal-contract-final.xml`）。

任务中心曾将查看详情的任务 ID 写入当前编辑 ID，导致编辑 A、查看 B 详情后，更新按钮仍显示 A，却提交到 B。浏览器复现的错误 PUT 与版本变化保留在 `task-identity-before.xml` 及其临时目录证据中。现已分离详情任务和编辑任务；只有显式打开任务/版本才能切换编辑目标，详情克隆使用详情任务 ID。浏览器验证了未打开编辑对象时查看详情不显示更新按钮、更新仍提交到 A、克隆来自 B 且不改变 A 的表单。相关界面与工作流回归 29 项通过（`task-identity-final.xml`）。

首轮在本机 8GiB RTX 5060 Laptop GPU 使用 Qwen/Qwen3-0.6B，模型文件从 ModelScope 获取，只加载本地 safetensors，禁用远程代码。该历史候选的来源与内容哈希记录在 `.models/Qwen--Qwen3-0.6B-provenance.json`；当前候选及其独立来源见后文。主仿真环境与 `.venv-sft` 隔离；CUDA wheel 从 PyTorch 官方索引获取并核验该索引公布的 SHA-256。

训练数据为注册场景派生的 576 条训练记录与 69 条验证记录，属于合成合同标签。相同语义场景族的时长和措辞变体只能出现在同一分区；正式验收请求不作为训练标签。完整 tokenizer 检查显示 645 条记录均可放入 12288 token 上下文，未截断长整星样本。标签含义是生成受注册表约束的 TaskSpec，再由可信编译器导出执行脚本。

当前候选加载命令（PowerShell，项目根目录，单独终端运行服务）：

```powershell
.venv-sft/Scripts/python.exe scripts/serve_modelscope_model.py --base-manifest .models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json --adapter training_runs/qwen25_coder15_taskspec_lora_v3/adapter --model-name Qwen2.5-Coder-1.5B-SatAgent-v3 --max-context 16384 --port 8031
```

在另一个终端验收；每次选择新的输出目录以保留先前结果：

```powershell
.venv/Scripts/python.exe scripts/evaluate_modelscope_taskspec.py --model Qwen2.5-Coder-1.5B-SatAgent-v3 --dataset-dir data/training/taskspec_lora_v3 --registry-constraints --output-dir reports/modelscope-heldout-new
.venv/Scripts/python.exe scripts/evaluate_modelscope_taskspec.py --model Qwen2.5-Coder-1.5B-SatAgent-v3 --dataset-dir data/training/taskspec_lora_v3 --product-chain --output-dir reports/modelscope-product-new
```

工作台模型服务页选择 ModelScope，地址填写 `http://127.0.0.1:8031/v1`，模型名填写 `Qwen2.5-Coder-1.5B-SatAgent-v3`；保存后在自然语言页选择该 Provider。当前服务输出上限 4096 token，上下文 16384；建议请求超时 300 秒。模型生成的是受注册表校验的 TaskSpec，再由可信编译器产生执行脚本；不会直接运行任意模型代码。

以下是保留的历史首轮环境和训练命令，不作为推荐模型或验收通过证明。若重新训练，请为新训练选择独立输出目录：

```powershell
$env:UV_CACHE_DIR = "$PWD/.uv-cache"
uv venv --python 3.12 .venv-sft
.venv/Scripts/python.exe scripts/fetch_torch_wheel.py
uv pip install --python .venv-sft/Scripts/python.exe 'third_party/wheels/torch-2.11.0+cu128-cp312-cp312-win_amd64.whl'
uv pip install --python .venv-sft/Scripts/python.exe --find-links third_party/wheels -r requirements-sft-lock.txt
.venv-sft/Scripts/python.exe scripts/train_modelscope_lora.py --max-steps 1 --smoke-longest --gradient-accumulation 1 --output-dir training_runs/longest_smoke
.venv-sft/Scripts/python.exe scripts/train_modelscope_lora.py --output-dir training_runs/qwen3_0_6b_taskspec_lora
.venv-sft/Scripts/python.exe scripts/serve_modelscope_model.py --adapter training_runs/qwen3_0_6b_taskspec_lora/adapter --max-context 16384
.venv/Scripts/python.exe scripts/evaluate_modelscope_taskspec.py --model Qwen/Qwen3-0.6B-sat-lora --output-dir reports/local_requirements_completion/lora-heldout
.venv/Scripts/python.exe scripts/evaluate_modelscope_taskspec.py --model Qwen/Qwen3-0.6B-sat-lora --product-chain --output-dir reports/local_requirements_completion/lora-product
```

训练或 smoke 完成不等于任务验收通过。`evaluate_modelscope_taskspec.py` 只发送验证记录的 system/user 消息，逐项检查模型来源、响应哈希、JSON、注册表、能力/参数/时长/事件/输出，并执行编译后脚本；失败内容不会用标签修补。默认固定选取每个验证场景族一条样本，`--all-validation` 执行全部验证记录。`--product-chain` 另行检查正式路由下的 ADCS 创建、增量修改和整星创建，要求真实模型调用证据且执行产品导出的脚本，不接受模板回退。

2026-10-25 基座实际结果：`base-heldout-cudnn-prefill/report.json` 为 0/6 完整通过，均返回了非严格 JSON 内容；`base-product-cudnn-prefill/report.json` 为 0/3 完整通过。后者 ADCS 创建可验证且已编译，但结构化输出哈希缺失；增量修改因前置创建没有完整通过而未执行；整星创建生成重复事件 ID，被拒绝。它们说明原始模型尚未满足本次验收，不能解释为所有自然语言都失败，也不证明微调后会改善。

首轮正式训练已完成于 `training_runs/qwen3_0_6b_taskspec_lora/`，使用完整 576 条训练记录、69 条验证记录，3 epoch / 216 个优化步，状态为 `TRAINED_NOT_TASKSPEC_ACCEPTED`。`lora-heldout/report.json` 仅 1/6 完整通过；`lora-product-guarded-merged/report.json` 仅 1/3 完整通过。存在输出字段、运行后端、事件与时间约束错误，不能将低验证损失当作可发布模型。

第二候选为从 ModelScope 下载并逐文件核验的 `Qwen/Qwen2.5-Coder-1.5B-Instruct`，来源清单为 `.models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json`。`qwen25-base-heldout-v2/report.json` 为 0/6：各条均因非严格 JSON 失败。`qwen25-base-product/report.json` 为 0/3；ADCS 配置可校验、可编译，但原始响应带 Markdown 围栏，严格结构化输出证据未通过。

数据复核发现旧标签存在输入未说明的采样间隔。新建并冻结的 `data/training/taskspec_lora_v3/` 明确给出采样间隔、积分步长、模式和模板参数；576/69 条记录仍按场景族隔离，645 条输入与标签配置核对全部通过。实际分词最长 11833 token，未截断。训练加载器现在拒绝即使重新计算过哈希、但输入与标签配置不一致的样本。JSON 服务提示词的示例也改为 JSON，避免同时要求 JSON 却示范 YAML。相关回归 `sft-v3-final-tests.xml` 为 27 项通过。该版本的独立基座结果写入 `qwen25-base-heldout-v3/report.json`；不能把前版分数直接当作它的基线。旧数据、训练和失败报告保持原样。

冻结版本的基座测试已完成，为 0/6，均在严格 JSON 解析处失败，原始响应与模型来源证据保留。`training_runs/qwen25_coder15_v3_longest_smoke/` 完整最长样本训练、反向传播与验证通过，耗时 43.28s，CUDA 分配峰值 7524256256 bytes、预留峰值 8378122240 bytes，产物仍为 `SMOKE_TRAINING_ONLY`，不能作为正式模型提供服务。

第二轮正式训练已完成，目录为 `training_runs/qwen25_coder15_taskspec_lora_v3/`，日志为 `reports/local_requirements_completion/qwen25-v3-full-training.log`；使用完整 576/69 条记录，完成 3 epoch / 216 个优化步。训练来源记录仍保持 `TRAINED_NOT_TASKSPEC_ACCEPTED`，不能用训练结束或低验证损失替代任务验收。

真实最终 LoRA 的原始冻结提示留出结果为 **5/6**（`qwen25-lora-v3-heldout-coverage/report.json`），唯一失败为 Python 机动模型被标成 Basilisk 组合后端。该失败保留，脚本未执行。生产路由下的 ADCS 创建、增量修改和整星创建 **3/3** 通过（`qwen25-lora-v3-product-coverage/report.json`），包含模型来源、响应摘要、无模板回退、编译脚本执行、实际有输入证据的物理检查和封存校验。

机动能力注册表现已明确 `accepted_task_backends: [python]`，校验器会拒绝错误的 Basilisk 后端声明，生产提示词也显示该约束。另行复测在 system/user 输入上补充来自注册表的后端声明，**6/6** 通过（`qwen25-lora-v3-heldout-registry/report.json`）。该报告明确 `prompt_policy=registry_augmented`，不冒充原始提示下的成绩，不传入或抄写参考标签。原始评测器快照、原始失败与冻结训练数据均保留。相关约束和执行回归 19 项通过（`backend-contract.xml`）；645 条冻结标签仍全部通过当前验证与编译（`sft-v3-runtime-contract-backend.json`）。当前正在运行全部 69 条验证记录的独立生成验收，不能把六条结果外推到全部自然语言请求。

使用当前候选的真实 tokenizer 复核冻结数据的生成预算，645 条记录的目标输出最长为 1914 token、提示最长为 9919 token，没有目标超过服务 4096 token 上限；输入加请求输出上限也在 16384 上下文内。逐记录结果见 `sft-v3-generation-budget.json`。这是预算可行性检查，不是模型准确率。

后续模型验收使用 v2 报告协议，除运行原始导出脚本外，将同一份生成 TaskSpec 交给真实生产计划与执行链，核对输入绑定、执行状态、物理门禁、有限数值遥测及封存制品哈希。`runtime-verification.json` 保存完整验证结果，物理断言失败也必须判为失败。该检查不宣称目标卫星飞行有效性；此前 v1 报告不追认这些新增检查。增量修改也已限制为仅接受本轮明确要求变更的参数，防止只改时长或追加曲线时重置已有硬件参数。执行验收、增量修改和本地模型服务回归见 `ai-execution-final.xml`（16 项通过），其中包含真实执行成功但物理断言失败的负例。

`scripts/audit_capability_output_surfaces.py` 对 24 个组件的默认配置分别编译并实际执行，核对实际输出、注册表和界面选项。`component-output-surfaces-final.json` 覆盖 166 个数值遥测字段，四类检查通过；该结果只覆盖这些默认配置，不代表所有故障模式或目标卫星标定通过。共享默认输出定义及提示词回归见 `output-default-tests.xml`（20 项通过）。

## 真实浏览器与运行归档

采样修复后的真实浏览器复测 `native-browser-sampling/report.json` 为 PASS，14 项检查全部通过，包含离线 SPICE/WMM 原生整星执行、实时暂停/单步/停止、原始历史与 SEALED 归档、环境结果展示、页面与 HTTP 无失败。布局和键盘检查仍以各自报告为准；这次工作流检查不等于任意场景与屏幕尺寸均已覆盖。

`scripts/run_native_browser_e2e.py` 启动独立的真实 HTTP 服务，由 Chromium 页面创建并执行 20s 原生整星 SPICE/WMM 仿真，再创建原生独立进程实时会话、启动、暂停、单步、停止并等待归档完成。没有替换物理运行器或网络响应。`native-browser-final/report.json` 已通过：21 条离线遥测、真实原生运行标识、两条链路的 SPICE/WMM 环境、实时原始历史、工作线程退出、SEALED 归档、页面无异常和 HTTP 无失败。

该检查发现并修复了正常运行时对未生成 FMEA/故障追溯制品的 404 请求，也修复了 Windows 作业对象保护被误标为 Linux 机制的问题。环境选择与 UTC 历元现在按常用参数展示。原生 `finalize()` 的结果保存在工作区 `runtime_result.json`，随后复制进受清单哈希约束的 Run Bundle。`/interactive/sessions/{id}/runtime-result` 校验密封清单与该制品摘要后才返回结果；制品或清单被改动均返回 409。它是运行观测结果，不是原生状态检查点。

## 持续墙钟验收

2026-10-27 的进一步复核发现两个真实校验缺陷：没有对应输入仍将检查列为已执行；连续工作区的详细物理问题超过 2048 条就使封存失败。现由批式、有限会话、连续流式共享覆盖累加器：只有有证据的检查进入 `checks_run`，无数据守恒残差时为 `NOT_EVALUATED`；明确要求却缺证据时为 `INCONCLUSIVE`，严格模式失败。默认不适用项不冒充通过。原生 SOC、Kelvin 温度、燃料和功率字段也已接入有相同语义的检查。

2048 现在是详细问题样本的上限，不再是告警总数限制；所有警告/失败都有准确总数、按检查与严重性分组的计数及首末位置，原始遥测保留完整位置来源。当前覆盖回归 23 项、封存与执行联动 36 项、整合回归 61 项通过（`physical-evidence-current.xml`、`physical-validation-final.xml`、`physical-coverage-final.xml`），有重复用例，不合并总数。界面明确显示部分物理覆盖和未评估项；真实 HTTP/Chromium 原生流程 20 项通过（`native-browser-physical-coverage/report.json`）。

最早的 `endurance-24h` 已于 2026-10-26 跑满 86430 秒墙钟，但封存失败，原因是旧 2048 条详细问题上限；原状态保留 `FAILED`，见 `failure-analysis.json`。使用新校验器对其原始记录只读重审，完整哈希链 288102 条记录通过，其中 144051 帧原生轨迹没有非有限数值，实际周期约 0.6 秒；准确计数 101172 条警告，详细样本 2048 条，采样峰值 RSS 47857664 bytes。重审未修改原进度或原遥测清单，也不将旧失败改成新版本 24h 通过（`endurance-24h-readonly-reaudit/report.json`）。

由于校验代码已修正，先前 `endurance-24h-aligned` 的旧版运行即使完成也不能认证本次版本。新版先经 15 秒原生冒烟并 SEALED（`endurance-coverage-smoke/progress.json`），随后于 **2026-10-27 00:32:16 UTC / 08:32:16 北京时间** 启动独立 86430 秒墙钟试验，目录 `endurance-24h-coverage/`。v3 来源清单将物理运行代码、交互封存代码、物理验证目录、ValidationOutcome 和 Run Bundle 校验器一起绑定，并校验运行器自身前后未改；7 项来源门禁回归通过（`endurance-source-v3.xml`）。本轮冻结指纹为 `b6cc818336ef2e0ccbb801e415be4c480546fb3d20bb7d79e9b93ee6b4cb5f16`。新时长独立计算，不拼接旧试验；当前仍未通过 24h 验收。

2026-10-26 独立实测发现：原生步长 0.2s、请求采样 0.5s 时，实际记录周期为 0.6s。现在默认 0.5s 连续会话采用 0.1s 积分步长；显式不对齐的原生采样和遥测流周期均拒绝执行，不再静默改变采样频率。单次、分段、轨道环境和连续会话等回归为 46 项通过（`native-sampling-broad.xml`）。新增原始遥测周期审计覆盖所有轮转/压缩段，审计相关回归为 11 项通过（`native-cadence-audit-tests.xml`）。冻结训练数据 645 条仍全部通过当前验证与编译（`sft-v3-runtime-contract.json`）。

原生状态审计、首步诊断、时钟/FSW 实验和执行器消融等另 27 项回归通过（`native-invariant-sampling.xml`），证明本次变更未破坏这些已有检查；不代表跨进程整星恢复已实现，相关拒绝门禁仍保留。

`endurance-sampling-smoke/progress.json` 的 15s 原生 SPICE/WMM 冒烟已 SEALED；32 帧全部严格满足 0.5s 周期，源码前后指纹一致。该短测不计作 24h 通过。新版本独立 86430s 墙钟试验于 2026-10-26 01:39 UTC 启动，目录 `endurance-24h-aligned/`；同时启动 `spice-performance-formal-sampling.json` 正式性能复测。新版耐久运行器记录源码前后指纹，源码变化将阻止合格结论。

原试验继续保留，不删除或覆盖。但其 0.2s/0.5s 配置存在上述采样偏差，即便完成原定墙钟时长，也不能证明采样要求或修复后版本已合格；旧运行器的原结论须结合新版独立原始遥测审计解读。

`scripts/run_native_endurance.py --output-dir reports/local_requirements_completion/endurance-24h` 已于 2026-10-25 13:16 UTC 启动，计划墙钟 86430s，1 倍速、SPICE/WMM、同一原生实例、5s 独立观测间隔。短测只用于核对记录器与归档链路，不计作 24h 通过。进度在 `endurance-24h/progress.json`，证据为工作区内的 `continuous_endurance_evidence.jsonl`。任何中断、采样缺口或物理运行失败都必须保留，不能重新启动后累计为一次连续运行。最终审计最多给出 `REVIEW_ELIGIBLE`，仍需人工评估，不自动批准跨进程恢复或飞行任务使用。

## 外部轨道参考对照

另对通用小卫星原配置执行了 7200 秒离线全工况检查，实际角位移覆盖约 1.238 圈，721 帧记录包含光照/食段和地面几何可见性的变化（`generic-leo-orbit-cycle/report.json`）。执行、数值有限性和制品封存检查均通过，但**任务验收失败**：太阳翼预测最低 197.283 K，低于模型声明的 200 K 下限，约 320 秒采样区间不满足温度约束。载荷预测最高 339.945 K，未越过其 340 K 上限。存储最高达到容量的约 99.998%，不能根据 600 秒短测声称持续任务的温度和数据预算均满足要求。

这些是声明的八节点简化热模型结果；当前模型没有地球红外、反照率热流及完整表面朝向/辐射视因子，不能将此低温预测等同于真实硬件会失效。推荐模板已显式披露这些遗漏和起始瞬态参考的适用边界；来源和模板传播回归 18 项通过（`generic-scope-cycle.xml`）。`generic-leo-orbit-cycle/operating-envelope.json` 保留具体时间、阈值、数据摘要及限制；PNG/SVG 图展示了整圈响应。`comm.downlinked_bits` 在此原生路径是记录间隔内矩形积分估计，不是累计下行计数；下行速率与几何可见性分别展示，不能将几何可见等同于可交付链路。

仅将同一输入的积分步长从 0.2 秒降至 0.1 秒，7200 秒配对复测同样完成并保留热约束失败（`generic-leo-orbit-cycle-step01/report.json`）。721 个对应采样点的最大位置向量差为 2.69 mm，载荷温度差为 0.00458 K，太阳翼温度差为 0.01783 K，SOC 差为 0.0000185。细步长太阳翼最低 197.2829 K，仍有 32 个低于 200 K 的样本。两种分辨率只给出本窗口的观测差异，不能当作严格离散误差上界、全工况精度承诺或硬件标定。两个实际运行均由新校验器确认封存完整性。

下行数据量和载荷产数的界面标题已明确为“本记录间隔”，并补充 bit 单位；字段与来源相关 8 项回归通过（`orbit-output-labels.xml`）。范围披露更新后的真实 HTTP/Chromium 原生离线与实时流程 18 项通过（`native-browser-engineering-disclosure-v2/report.json`），无页面或 HTTP 错误。

已冻结 NASA/JSC/FOD/TOPO 于 2026-10-25 发布的 [ISS OEM 规划星历](https://nasa-public-data.s3.amazonaws.com/iss-coords/current/ISS_OEM/ISS.OEM_J2K_EPH.txt)，本地目录 `data/reference/nasa_iss_oem/4bbf55dc4a3a4620/`，原文 SHA-256 为 `4bbf55dc4a3a46205a421482e7595e1efcc9d68356425af4f54a2148bd355284`。输入核验为 Earth/EME2000/UTC，位置和速度由 km、km/s 转成 SI；不插值、不混入其他坐标系或时间系统。解析器 9 项测试通过。

`scripts/benchmark_iss_oem.py` 只用 2026-10-26 00:00 UTC 的第一个状态初始化相同的通用原生 ADCS 轨道模型，对照后续一小时内 15 个独立参考时刻，不使用后续状态拟合或调参。`nasa-iss-orbit-comparison-final/report.json` 实测结果如下，报告状态为 `MEASURED_NOT_QUALIFIED`：

| 环境 | 后续状态位置 RMS 残差 | 最大位置残差 | 速度 RMS 残差 |
|---|---:|---:|---:|
| 地球点质量 simplified | 20040.69 m | 40395.17 m | 23.6983 m/s |
| 地球 J2＋日月引力 spice_wmm | 64.75 m | 148.37 m | 0.08851 m/s |

原始配置、遥测、逐时刻径向/沿轨/法向残差 CSV 与可导出的 PNG/SVG 均保留。该结果只证明这个窗口的外部规划参考对照，**不是原始飞行遥测标定或 ISS 硬件复现**；通用航天器质量和姿控设备不代表 ISS 配置。大气阻力、高阶地球引力与 EME2000/J2000 框架偏差尚未补偿，参考窗中的机动也未独立排除。尚未收到用户精度验收阈值，不据此认证飞行有效性或升级参数证据等级。

## 数值门禁边界

本机 CUDA 检查与实际矩阵计算通过，`uv pip check` 确认隔离环境的 49 个包兼容。Windows 官方 wheel 的 `is_flash_attention_available()` 为 false，默认 SDPA 长样本冒烟因显存压力被中止并保留记录。显式 cuDNN 注意力的完整 10473-token 冒烟于 2026-10-25 完成，单步及验证约 19.4s，CUDA 分配峰值 3649195008 bytes，预留峰值 4081057792 bytes；产物标记为 `SMOKE_TRAINING_ONLY`，服务拒绝将其当成已完成微调加载。推理采用 cuDNN 预填充与数学 SDPA 单 token 解码，独立 BF16 数值/梯度相对误差小于 1%，未来 token 不影响过去输出。证据为 `sft-gpu.json`、`sft-loss-equivalence-torch211.json`、`sft-attention-equivalence.json` 和 `training_runs/longest_smoke_cudnn/training_provenance.json`。

SPICE/WMM 耦合图的步长细化实测约二阶收敛；不能因底层积分器为 RK4 就声称整个耦合系统为四阶。独立点质量 RK4 用例另行确认约四阶。当前数值试验是工程模型内部一致性证据，没有目标卫星实测残差、标定和不确定度证据。

正式性能报告测量的是软实时仿真，4 小时指仿真时间；30 分钟漂移测试才按墙钟持续运行。Windows 上 FD 数量不可用（报告为 null/-1），不得将 FD 门禁字段解释为已测量 Windows 句柄稳定性。

证据分别保存在 `reports/local_requirements_audit/`（初审）和 `reports/local_requirements_completion/`（功能补齐）。最终验收必须逐项重新检查实际代码、运行产物及浏览器行为。
