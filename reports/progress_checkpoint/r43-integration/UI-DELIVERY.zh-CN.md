# 工作台界面交付说明

本次调整让配置、校验和运行成为首屏重点：桌面能力库收进“选择仿真对象”，主题、字体、Token和正常版本信息收进“界面设置”。服务异常、繁忙或异常队列、版本不一致仍直接显示。两处入口支持键盘展开，Esc收起并返回焦点；原有业务操作和控件ID保留。

尚未选择运行时，结果栏缩窄并取消大块空白；读取、失败或已有结果时恢复展示。结果列可以纵向滚动，曲线不再被父容器压缩裁切，展宽、全屏、页签和图例继续可用。390像素宽度手机的115%/130%字体使用“校验、运行”双列和下一行“更多操作”，避免按钮文字竖排。

生产改动共四个文件：index.html、compact-workbench.css、compact-workbench.js，以及app.js的两行状态清理。选择另一次运行时，同时清除telemetryOverview和旧采样覆盖说明；读取失败时显示“尚未加载”，重新成功载入后再显示该运行的实际样本信息。未改变API、请求版本保护、任务编辑归属或canonical文本与规划链路。

组件验收为45项既有浏览器/静态回归、53项实际HTTP/Chrome检查，17张最终截图均已逐张查看。真实2秒整星Basilisk运行成功，得到11个样本；后续布局检查重放其已封存数据，没有重复物理运行或模型调用。桌面编辑区由728增至984像素；该数字是1440宽度下的R40组件测量。

可直接查看[桌面首屏](images/empty-1440.png)、[1366×768真实曲线](images/native-chart-1366.png)、[390宽度与130%字体](images/empty-390-font130.png)、[手机大字体全屏曲线](images/native-fullscreen-390-font130.png)和[读取失败状态](images/owned-run-browser-induced-read-error.png)。完整来源与哈希见[图像审查](LAYOUT-EVIDENCE.json)及[完成索引](R40-SUMMARY.json)。R40已合入R43，R45仅更新测试导航。R43安装态模型联合链有独立记录；原R43全套五项导航失败保留，R45全套仍PENDING。组件结果不直接改标为整版通过，也不称界面已经完美。

设计采用[Carbon UI shell header](https://www.carbondesignsystem.com/building-blocks/core/components/ui-shell-header/guidelines)与[左侧导航](https://www.carbondesignsystem.com/building-blocks/core/components/ui-shell-left-panel/guidelines)的层级和收展思路，以及[Grafana panel overview](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/panel-overview/)与[panel inspector](https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/panel-inspector/)的聚焦查看、图例和按需检查思路。这些是公开交互参考，未引入相应组件库或复制产品界面。

本次应用了frontend-design的设计计划、保留现有token与实际看图迭代方法，以及awesome-design-md中IBM/Carbon参考的结构化控制台方向；未另建主题。
