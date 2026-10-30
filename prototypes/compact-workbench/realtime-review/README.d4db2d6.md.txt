# 实时页精简候选验证

31项浏览器检查通过，来源为`browser-report.json`；实际API与原生执行，没有模拟遥测或替代答案。持续会话使用独立监督式原生子进程，限时会话使用另一进程内原生预览，两者前端字节相同。已覆盖真实遥测绘制、遥控ACKED、暂停、单步、继续、停止封存、键盘披露、手机停止按钮、亮暗主题、130%字体以及1920/1366/768/390宽度。474个原静态ID保留。11张最终截图及前后对比2张截图均已实际查看。

同一已完成原生会话、同一API和数据、1366×900，对照仅替换原版UI资源：遥测图起点1654.7→552.2px，持续页已渲染文字1992→1394字符。后者包含屏幕外文字，折叠内容仍可访问，不能写成首屏字数或信息删除比例。对比记录在`layout-comparison.json`。

主要变化：优先展示状态和遥测；会话列表前置；创建表单按选择状态收起且允许手动重开；诊断信息后移；停止、错误及归档状态保留可见；限时页增加文字控制和归一化说明。未通过缩小原字号换取空间。

另有两项运行链路修正，不能算纯视觉变化：默认0.5秒记录周期不能由0.2秒积分步长整除，候选改用0.1秒积分，保留记录周期；预览服务器补充独立WebSocket依赖。主uv环境、生产源码和原实际24小时实例均未修改。WebSocket包文件摘要和预览模式随独立复核保留。

本机重建：

```powershell
uv pip install --python .venv/Scripts/python.exe --target reports/local_requirements_completion/compact-ui-websocket-deps --requirement prototypes/compact-workbench/requirements-preview.txt
.venv/Scripts/python.exe scripts/preview_compact_workbench.py --native-runtime in-process
```

专门验证监督式持续会话可另开预览，使用`--native-runtime supervised`。该部署模式拒绝限时会话是后端现有政策；不在前端偷偷切换运行语义。进一步完善运行时能力提示和跨类型会话列表仍列为后续集成工作。

`review-source.py`与`comparison-source.py`是本地实测脚本原样快照，含本次目录和回环端口，复用时需指向新预览，不是通用安装命令。`history/`保留先前阶段报告：CSP等待方式、非空服务的夹具前提、切换会话的披露错误、误用EXECUTED而非ACKED的测试预期、部署类型拒绝、采样不对齐、缺失WebSocket，以及主题过渡中的旧截图问题均不删除。v5、v9曾通过较早范围的检查，不冒充最终v10结果。

本候选未与AI事件修复整合、未获整包回归或生产耐久资格；不声明全部页面或所有前后端交互已经完善。采用[Carbon表单的任务分组与按需披露](https://www.carbondesignsystem.com/building-blocks/core/patterns/forms)和[Grafana的图表聚焦与工具栏组织](https://grafana.com/docs/grafana/latest/visualizations/dashboards/use-dashboards/)，使用`frontend-design`两遍式设计流程。
