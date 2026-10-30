> 以[当前逐项验收状态](current_requirements_status.md)为准。以下保留各修订的历史记录，其中“当前”“待完成”等措辞仅适用于对应修订，不代表最新状态。整体完成状态仍未证明。

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


# SAT-SIM 0.7.8 验收记录（进行中）

记录日期：2026-10-27（Asia/Shanghai）。总体状态：**尚未完成最终验收**。本记录只汇总下列已有证据；未执行全仓测试，也不继承历史版本的通过计数。

整圈补测已完成：`thermal-environment-full-cycle/report.json` 中四组7200秒原生任务约束均PASS，来源前后一致；温度步长差最大0.009947K，反照与红外求积细化差最大0.041952与0.023765W/m²。较低参数情景仍仅有0.0178K太阳翼低温余量。79项相关路由与拒绝回归通过。以下“较高参数情景仍执行”是较早快照，已由本段完成状态更新；性能、AI和24h尚未由此获得通过。2048 token上限的独立AI复测也已在300秒后超时失败，未编译或执行替代脚本，仍需解决生成时延问题。

最新补充：新热环境两种步长的7200秒运行均通过，采样温度最大差0.009947K；较低红外/反照情景也通过，但太阳翼最低仅200.0178K，不能宣称稳健设计。较高参数情景和求积细化仍在执行。实时短测 `thermal-native-realtime-smoke/progress.json` 已SEALED、前后来源指纹一致；新源码独立24h为 `endurance-24h-finite-earth-thermal/progress.json`。正式性能 `spice-performance-finite-earth-thermal.progress.jsonl` 已记录10倍速实际约7.44倍、未满足门槛，其余正式时长仍在执行。AI新请求先因SPICE路由误拒绝，修复后发生300秒模型超时；两个失败结果保留，2048 token上限的独立复测仍在执行，未用模板替代。

最新浏览器报告为 `native-browser-finite-earth-disclosure-verified/report.json`：50项通过，包含外热流实际执行、工程热边界可展开查看，以及原有四屏宽/四字号交互检查。异常中止后新建场景 `native-failure-recreation-finite-earth/report.json` 12项通过。以下旧表及旧路径保留各自版本快照，其中的“最新”只对当时快照成立，不能覆盖本段的待验收项。

当前正在补齐原生热环境：太阳直射、地球红外、朗伯反照分别进入真实热节点积分，太阳翼热量扣除实际 EPS 发电功率。几何核22项、来源绑定与原生环境合计31项检查通过；独立 Basilisk 反照对照三相位及离线/分段一致性等首轮5项通过。7200秒整圈、步长收敛及参数敏感性仍在执行。原先 `endurance-24h-mission-final/` 和正式性能8/8属于此前太阳热输入模型，不能认证这次热环境源码。未改变安全阈值以获得通过。

前端最新浏览器证据为 `native-browser-responsive-accessible-final/report.json`：47项通过，覆盖真实HTTP原生离线/实时流程、390/768/1366/1920像素宽度及90/100/115/130%字体。手机工具栏和模板库折叠，同一组会话控制移至底部，焦点随实际创建/选择进入当前状态；未增加重复控制或自动启动。下表更早计数作为各自版本证据保留。

用户确认工程目标为通用近地小卫星仿真，披露可追溯假设与误差边界；不要求指定型号的真实硬件或飞行验证，也不作该等级声明。异常恢复范围为保留失败证据并创建新场景；同一物理状态跨进程续算不在本轮验收范围。

| 要求 | 已验证内容 | 尚待完成或适用边界 |
|---|---|---|
| uv 创建环境 | 当前环境及独立离线安装的 doctor 均为 12/12；依赖检查通过 | 证据：local_requirements_completion/doctor.json、offline-doctor.json |
| 单次离线与实时仿真 | 任务摘要、全程热越限、分段一致性及封存回归92项通过；异常恢复12项通过；最新版正式性能8/8通过，30分钟墙钟漂移P95为13ms | 独立24小时进度为 endurance-24h-mission-final/progress.json；此前代码及运行器版本不继承为当前通过时长 |
| 通用近地工程模型 | SPICE/WMM、UTC、J2 与日月引力；参数假设、适用域和未建模效应披露；600 秒工程场景 9 项通过 | 7200 秒整圈执行成功，但太阳翼低温约束失败，存储接近满载；热模型遗漏地球红外与反照率，不能把预测温度当成硬件失效证据；NASA 规划轨道残差不构成任意工况精度保证 |
| AI 辅助创建脚本 | Qwen2.5-Coder-1.5B LoRA 已完成216步；最新传输契约下真实生产创建/修改3/3通过；原始提示5/6，注册表增强6/6；全部扩展验证68/69通过 | 扩展整批状态FAIL：电池容量损失幅度0.3被生成为1.0，已拦截且未执行；修改流程仍发生一次格式重试，有限合同样本不能外推全部请求 |
| 前后端交互与展示 | 参数来源与工程范围随编辑进入运行记录；最新真实 HTTP/Chromium 原生流程23项通过，显示部分物理覆盖与未评估项，数据存储标签与主要指标单位已复核 | 仅覆盖已测试布局和流程，不能声称“无可挑剔” |

最新代码修复了“执行完成掩盖任务失败”和“分段记录清理遗漏早期热越限”。失败验收报告写入工作区 `validation_outcome.json`，通过封存门槛保持。最新证据为 `mission-history-verified.xml`、`native-failure-recreation-mission-history/report.json`、`native-browser-mission-history/report.json` 和 `endurance-mission-history-final-smoke/progress.json`。短测不代表24小时通过；运行耐久审计不代表任务约束通过。以下较早条目为历史记录，当前长测路径以上表为准。

传输契约专项 `json-draft-envelope-verified.xml` 34项通过，最新真实模型生产证据为 `qwen25-lora-v3-product-envelope-aligned/report.json`。结果概览已隐藏内部版本字段，明确区分数据存储和储能，并为主要指标补齐单位。当前环境 `doctor-mission-current.json` 为12/12通过，依赖检查无冲突；`script-governance-mission-history.json` 为92脚本、0错误、0警告。

当前界面证据以 `native-browser-overview-units-final/report.json` 的23项通过及同目录截图为准；此前20项流程为较早快照。实际模型本次三条生产流程耗时约26/56/109秒，仅修改流程发生格式重试，单次观察不代表通用延迟保证。

补充的 `overview-units-routing-closed.xml` 36项、`json-envelope-routing-verified.xml` 38项回归通过；同时修复了 EPS 分系统请求因电池 SOC 输出词被误选为电池单机的规划问题。最新只读复核 `endurance-24h-readonly-mission-history/report.json` 在旧原始轨迹中检出1078条热安全失败，保留101172条警告及准确计数；原始进度、遥测清单均未改写。该复核不是新物理运行，旧摘要PASS不再被用作全程任务通过证据。

最近修复与证据（均位于 `reports/local_requirements_completion/`）：

- 最新正式性能 `spice-performance-formal-mission-history.json` 为PASS，源码前后指纹一致；4小时检查指仿真时间。
- `event-production-contract-final.xml` 75项通过；`event-contract-product/report.json` 证明生产门禁拒绝旧的幅度错误回答，随后独立真实模型调用正确生成并执行同一用户要求。原68/69失败基准不改写。
- 旧冻结数据事件一致性为633/645，其余12条为投递字段不一致；`sft-v4-event-contract-evidence.json` 记录新数据588/69条全部通过输入/标签、事件及编译校验，保留原645条场景和时长并新增12条工程场景。新数据尚未训练，当前服务仍使用原v3模型。

- `native-failure-recreation/report.json`：12 项通过，旧失败证据保持不变，新原生场景从零运行并封存。
- `sft-v3-reference-execution/report.json`：原始参考任务复核 5/6，通过与失败均保留。
- `storage-conservation-diagnosis/diagnosis.json`：依据官方 Basilisk 2.11.0 存储实现推导逐节点、逐物理步整数 bit 舍入上界；保留原始守恒残差。
- `sft-v3-reference-execution-rounding/report.json`：同一冻结参考标签复测 6/6。这是参考任务可执行性，**不是 AI 生成准确率**。
- `storage-rounding.xml`：6 项通过；`composite-rounding.xml`：27 项通过。两组含重复用例，不合并成唯一用例总数。
- `seal-binding.xml`：24 项通过，覆盖封存哈希绑定、篡改反例、真实任务执行和实时封存/恢复。
- `seal-contract-final.xml`：扩展清单条目/路径/大小/读取错误校验后，49 项相关检查通过。
- `task-identity-before.xml` 保留了编辑 A、查看 B 详情后错误更新 B 的浏览器复现；修复后 `task-identity-final.xml` 的界面及工作流 29 项通过，验证更新 A 与克隆 B 使用各自正确目标。
- `sft-v3-generation-budget.json`：645 条冻结训练记录的目标输出最长 1914 token；预算可容纳，不代表模型能正确生成。
- `generic-leo-orbit-cycle/report.json`：7200 秒实际原生运行，覆盖约 1.238 圈；721 帧有限输出与封存校验通过，任务热约束失败。具体热/存储范围和遗漏见同目录 `operating-envelope.json` 与图表，失败未改写为通过。
- `generic-scope-cycle.xml`：工程范围与模板传播回归 18 项通过。
- `generic-leo-orbit-cycle-step01/report.json`：仅步长从 0.2 秒减为 0.1 秒，721 个对应样本的最大位置差 2.69 mm、太阳翼温度差 0.01783 K；两个分辨率均保留热约束失败，不能据此声称硬件有效性或严格全局误差上界。
- `orbit-output-labels.xml`：本记录间隔产数/下行量标题和单位相关 8 项回归通过。
- `native-browser-engineering-disclosure-v2/report.json`：最新披露与界面下的真实 HTTP、Chromium、原生离线/实时流程 18 项通过，无页面或 HTTP 错误。
- `existing-bundle-compatibility.json`：9 个真实既有封存包通过更严格校验，任务通过/失败状态保持原样。
- `storage-engineering.xml`：首次工程回归 29 通过、1 失败，失败原因是本报告此前缺失；保留原结果。补充当前真实验收记录后，`storage-engineering-final.xml` 复测 30 项通过，未改写测试要求。
- `script-governance-recovery.json`：92 个脚本，0 错误、0 警告。

长任务进度应读取 `endurance-24h-mission-final/progress.json` 与 `qwen25-v3-full-training.log`，不得将此静态快照当作任务已完成。详细方法与限制见 `docs/functional_completion.md`。

2026-10-27 补充证据（路径相对 `reports/local_requirements_completion/`）：

- `physical-evidence-current.xml` 23项、`physical-validation-final.xml` 36项、`physical-coverage-final.xml` 61项通过，含重复用例不能合并总数。修复缺输入仍列已执行的假阳性，并用有界详细样本与准确计数替代2048条告警后封存崩溃的旧行为。
- `endurance-24h/progress.json` 的原始结果为FAILED：86430墙秒运行完成但封存失败；`endurance-24h/failure-analysis.json` 保留诊断。
- `endurance-24h-readonly-reaudit/report.json` 只读重审完整288102条哈希链记录、144051帧轨迹，非有限数值0；警告101172条全部计数，详细样本2048条，采样峰值RSS47857664 bytes。原失败状态未改写，实际0.6秒周期也不符合0.5秒采样请求。
- `endurance-coverage-smoke/progress.json` 的15秒新版原生短测SEALED、源码与运行器前后指纹一致；`endurance-24h-coverage/progress.json` 才是当前独立24小时进度，不能累计旧版时长。
- `qwen25-lora-v3-heldout-coverage/report.json` 为原始提示5/6，Python机动后端选择错误；`qwen25-lora-v3-product-coverage/report.json` 为真实模型生产流程3/3；`qwen25-lora-v3-heldout-registry/report.json` 为独立注册表增强提示6/6。后者不传参考标签，不覆盖原始失败结果。
- `qwen25-lora-v3-all-validation-registry/report.json` 为全部69条记录的进行中结果；最终状态以该文件为准。
- `native-browser-physical-coverage/report.json` 的20项真实浏览器检查通过，明确显示部分覆盖与未评估的数据守恒；`agent-context-final.xml` 的14项多轮手改与上下文回归通过（协议控制流，不是模型准确率）。
- `backend-contract.xml` 的19项约束与执行检查、`sft-v3-runtime-contract-backend.json` 的645条冻结标签验证/编译、`script-governance-coverage.json` 的92脚本治理均通过。

历史Ubuntu/vLLM发布专项与RW_JAM诊断数据晋升专项没有继承为本轮通过。`release-qualification.xml` 为8通过、2失败，缺少原专项报告；原门禁保持，不把Windows局部验证或ModelScope结果冒充该专项通过。当前发布清单资格为IN_PROGRESS，模板数与运行时一致为58。
