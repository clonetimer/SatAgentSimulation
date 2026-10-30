# 当前需求验收状态（2026-10-31）

整体仍为 **ACTIVE / UNPROVEN**。当前审阅候选为 **R46**（App `aab77d74…f2b9`，Core `e81bf1cf…939e`），本机独立预览为 http://127.0.0.1:55960 。Root原运行时代码未被替换。

| 要求 | 已取得的同版证据 | 尚未完成 |
|---|---|---|
| uv环境与安装 | 独立Windows/Python3.12环境90依赖+1项目；新wheel安装来源与字节检查通过 | 不外推其他平台；候选不是最终release |
| 单次离线、实时仿真 | 全量2427通过、0失败、6跳过、2警告；真实GUI导出2脚本与同页Run共3次短native执行 | 新正式24h已从零启动、终态待验；异常中止保留证据、重建场景，不声称跨进程续算 |
| 通用近地小卫星工程仿真 | 原4 W/K导热链接、SPICE/WMM、假设和误差边界保留 | 物理warning1/42保留，strict=false，不能声称全部高保真、飞行验证或硬件标定 |
| AI创建/修改脚本 | 两个固定真实请求完成17%创建→42%修改→原plan/文本保真→下载/native/曲线；94流程+17收尾检查，11图实看 | 69仍RUNNING；前37条raw current25/37、legacy strict0、final current0且未执行。12条ADCS把1e-5生成为1e-15，AI质量未验收完成 |
| 交互与可视化 | 29项同版交互检查通过；生成重入、旧ZIP/装配代码写回有red→green证据；真实桌面/窄屏曲线已复核 | 工具栏仍偏密及常驻实现版本文字，待独立有限精简；不称完美 |

上述69数据是固定前37条的as-of快照，不能作最终得分；编译通过不等于参数正确或执行通过。协议fixture的5张图与真实模型/native图分开标注。新24h于2026-10-30 22:50 UTC从零启动，完整节拍采集及新实例专属防休眠租约均已核实；首次资产共享读取前置失败保留，修正只读共享方式后全部资产SHA仍匹配。旧Root实例62304墙秒的RUNNING/DEGRADED快照和2.8543958999216557秒峰值保留，不继承为新候选资格；主机存在并行模型/仿真负载。

见[R46进展包](progress_checkpoint/r46-interaction-ownership/README.md)、[全量结果](progress_checkpoint/r46-interaction-ownership/FULL-RESULT.json)、[真实GUI](progress_checkpoint/r46-interaction-ownership/real-ai-gui/public-summary.json)及[69部分快照](progress_checkpoint/r46-interaction-ownership/AI-QUALITY-ASOF.json)。本页此前原文已原字节归档至[R46历史快照](progress_checkpoint/r46-interaction-ownership/history/current_requirements_status.pre-current-r46.md)。R43/R45旧分数、原0/69、旧GUI失败及所有物理峰值保留，不跨版本相加或改判。
