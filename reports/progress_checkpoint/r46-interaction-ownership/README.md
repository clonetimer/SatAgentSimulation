# R46进展增量：全量与两场景通过，整体仍在验收

冻结候选App `aab77d74…f2b9` / Core `e81bf1cf…939e`。本包尚未发布为最终release，也不会自动覆盖仓库Root运行时。

- **完整回归：2427通过、0失败、6跳过、2警告**，2433项，1596.07秒；见[FULL-RESULT.json](FULL-RESULT.json)。
- **交互修复：29项同版定向通过**。其中5个新增竞态先有真实Chrome red，再green；原始失败/两次夹具错误均保留。`screenshots/`的5张图明确是mock Agent和延迟HTTP的**协议fixture**，0模型/物理执行。
- **真实模型与短原生链：2次新模型请求、2份下载脚本及1次同页native运行**；94流程检查、17收尾检查通过，11张原图经实际查看。本包仅复制获批8文件：脱敏摘要+7图，见[真实GUI证据](real-ai-gui/public-summary.json)。17%创建/42%修改、原plan和规范文本往返保持；物理warning1/42、strict=false和不能声称高保真原样保留。
- **安装候选：独立uv环境90依赖+1项目，安装来源/字节检查通过**。`dist/` wheel为已验证镜像原字节，约2.71MB；见[安装范围](INSTALLED-WHEEL.json)。当前仍为IN_PROGRESS。

**AI质量未完成。** 当前仅公布固定前37/69条的as-of：37次模型返回且编译，raw current25/37、legacy strict0、final current0；这些基准未执行。12个ADCS回答把请求`1e-5`写成`1e-15`，不能将编译成功当作参数正确。69仍RUNNING，不作最终评分，见[部分快照](AI-QUALITY-ASOF.json)。原冻结0/69与历史失败不重判。

**新正式24h已从零启动，终态待验。** 开始于2026-10-30 22:50 UTC，完整节拍采集和新实例专属防休眠租约已核实。首次资产共享读取前置失败保留；修正只读共享方式后全部资产SHA仍匹配。主机有并行模型/仿真负载；旧Root实例RUNNING/DEGRADED、2.8543958999216557秒峰值保留，不继承时长。通用近地工程假设和误差边界不代表飞行或硬件标定。图形工具栏正在独立R48候选中继续精简。

`source-overlay/`仅3个相对冻结R45的源码/测试路径，须核对WHITELIST中的原始beforeSHA；新增文件要求原来不存在。它是已发表R43—R45进展包之后的分层增量，不能假设freshclone前像相同，不删除额外文件，保留历史egg-info/CRLF/rawpreimage限制。

本包白名单列出所有实际文件、SHA与字节数。旧待验摘要、包内清单及Root当前入口原文已逐字节归档于`history/`；其PENDING只代表当时时点。当前以[public-summary.json](public-summary.json)的分范围数据为准。未复制环境、权重、provider配置、模型原文、数据库或全遥测，未修改R43封存包/运行时，未执行Git操作。
