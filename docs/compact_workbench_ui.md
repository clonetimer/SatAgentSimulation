# 精简工作台候选

本轮按用户反馈优化文字密度和操作层级，使用`frontend-design`与`awesome-design-md`。候选源码、设计取舍、原版/新版截图及验证记录见[原型目录](../prototypes/compact-workbench/README.md)。

主要操作保持“校验配置”和“运行仿真”，模板/保存/重置归入更多操作；说明按需展开，参数帮助在聚焦时出现，错误和单位保留。结果区突出PASS、制品完整性与真实曲线，技术ID和低频操作仍可访问。

相同默认整星表单，1366×900参数起点999px→446px；默认渲染创建页文本4372→674字符。该字符数包含屏幕外已渲染文字，不等于首屏字符数；折叠信息仍保留。474个原静态控件ID和默认表单值保留。22项浏览器检查通过，真实原生运行PASS/VERIFIED并实际绘图，7张最终截图已逐张查看。

在本机启动独立预览：

```powershell
.venv\Scripts\python.exe scripts/preview_compact_workbench.py
```

脚本输出新的回环URL，数据写入独立目录，Ctrl+C停止预览。源码复制、候选覆盖和资源均验证，生产src不改动。`--prepare-only`只构建预览目录。

当前实际24小时实例绑定原App4cce/Core59，因此候选尚未合入生产。后续实时／持续工作区也已有隔离实现和31项实际浏览器检查，11张最终截图逐张查看；同会话对照下遥测图起点约1655→552px。原22项结果仍只代表第一版创建／结果页，不合并成新版本完整回归。实时阶段同时修正了默认记录／积分周期不对齐，并用uv目标目录补充WebSocket依赖，详见[实时验证、依赖安装与运行模式](../prototypes/compact-workbench/realtime-review/README.md)。其余页面、运行时能力提示、与AI事件修复的整合和适当回归仍待继续；它不继承生产全套或实际耐久资格。

参考[Carbon表单规范](https://www.carbondesignsystem.com/building-blocks/core/patterns/forms)的分组、短标签和渐进披露，以及[Grafana仪表盘](https://grafana.com/docs/grafana/latest/visualizations/dashboards/use-dashboards/)的图表聚焦。学习交互组织，不复制品牌页面。
