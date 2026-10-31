# 界面减负：图形工具栏

已应用 `frontend-design`、`awesome-design-md`，参考[Carbon工具栏](https://www.carbondesignsystem.com/building-blocks/core/patterns/text-toolbar)、[Grafana面板](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/panel-overview/)及[W3C折叠控件](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/)，沿用原配色与布局。

- 流程和装配均突出“校验、生成Python、构建并运行”，编辑与工程操作按需展开。
- 精简说明、移除常驻版本兼容文案；错误、警告、运行状态继续直接显示。
- 原224个带ID按钮、输入框和选择器的属性保留；新增4个原生折叠组，支持键盘展开、Tab访问及Escape返回。

**验证：31项实际Chrome交互与26项工作台静态/API检查通过。** 包含原防竞态断言、桌面1440、窄屏390/130%字体、工程导入后二次导出内容相等。另在真实localhost预览检查了桌面和窄屏，7个HTTP资源与冻结源码一致，零页面错误。6张布局/预览图不代表模型或物理仿真验证；本轮没有新模型或native调用。

本机预览：http://127.0.0.1:56148 。这是独立源码预览，依赖环境未改；不声称已有R48安装包或R48全量2427项通过。R46的全量与真实AI/native证据属于其原版本，R48的715个生产Python文件及物理核心与R46相同。AI参数保真修复和正式24h终态仍待验收，整体目标未完成。

`screenshots/01-before-flow.png`与`02-after-flow.png`是同一CPU接口场景下分别重放R46、R48资源的受控对比；`03-after-assembly.png`及`04-project-keyboard-narrow.png`来自最终31项回归；`05-live-desktop.png`与`06-live-narrow.png`来自真实HTTP预览。图均已实际查看。

`source-overlay/`仅相对冻结R46的7个路径，应用前须匹配白名单beforeSHA；新文件须原先不存在。它是分层进展增量，不能假设fresh clone有相同CRLF及历史元数据前像。Root代码尚未替换，正式耐久使用独立R46源码。原两次旧文案预期失败保留于私有原始记录，未删功能断言、未调整物理阈值。
