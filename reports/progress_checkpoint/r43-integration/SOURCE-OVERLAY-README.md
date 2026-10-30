# 有限源码增量的使用边界

本目录仅供审查，尚未应用到Root。source-overlay包含87项差异中的86份精确后像；reports/current_requirements_status.md由Root另行基于最新终态改写，因此未复制。

应用前须逐项核对SOURCE-OVERLAY-MANIFEST.json的before SHA；null表示该路径应不存在。发现任一不同应停下审查，不能假定fresh clone或覆盖用户修改。after SHA绑定R45冻结原字节，CRLF不作自动转换。六份历史egg-info保留，不自动删除Root额外文件。wheel从独立staging构建，不能在冻结源码或Root生成egg-info。

dist中的2.7MB wheel是IN_PROGRESS安装候选的原字节镜像，已验证的868运行时payload与R45生产源码一致。R43/R45生产App相同，R45仅更改测试导航；该wheel不代表R45全套已通过，也不代表Ubuntu、新源24h或硬件标定资格。
