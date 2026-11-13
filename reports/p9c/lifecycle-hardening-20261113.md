# P9-C 场景生命周期与相机控制复核

基线：PR #129，cc759b96d0c77f30296961283b8c54470050489e。
源文件基线 Git blob：c9f37fa9544325b21b787e3745824146bf9a68b2（与本地复现文件逐字节哈希一致）。

## 发现与修复

- 原实现创建 Viewer 后立即宣称已就绪；现使用 Globe.gl 的 onGlobeReady 回调，再启动显示时钟和相机按钮。
- 为加载等待设置 15 秒超时；超时清理后可返回向导重试，不显示内部异常字符串。
- 每次进入生成独立代号；旧场景的延迟回调不能更新新场景状态或复活动画循环。
- 相机 controls 的用户 start 事件解除跟踪并取消未完成的视角过渡，后续显示 tick 不再抢回视角。
- 创建第三方实例后立即记录所有权，配置中途失败也可清理。
- 退出调用固定版本 Globe.gl 的 wrapper teardown，而不仅删除 canvas；取消本控制器的 RAF、timeout 和 ResizeObserver，解绑相机监听器。
- 复用官方文档和上游 teardown 实现，不变更地球引擎、Basilisk、TaskSpec、Run Bundle 或 CSP。

## 证据与边界

Node 受控时序测试：旧实现 2 passed / 7 failed；修复后 9 passed / 0 failed。覆盖影像未就绪、重复进入、退出后回调、旧实例回调、手动相机接管、加载超时、部分初始化失败、幂等销毁与 12 次进出。
这些测试执行真实生产控制器，但替换第三方事件与渲染依赖以控制时序，不能代替真实 GPU/WebGL 验收。

真实 Chromium 用例加强为 3 次完整进入/拖动接管/退出，拦截并拒绝所有外部 HTTP 请求，保存截图与 JUnit。CI 同时执行 Node 测试与 wheel 资源闭包检查；结果以本次提交工作流为准。

本地完整 pytest 环境探测止于 tests/test_native_thermal_environment.py 收集错误：ModuleNotFoundError: Basilisk。本地可获得的整库资料为 P9-B CI source snapshot，不冒充 P9-C 完整精确源码或全库通过；P9-C 生产控制器文件已单独验证精确 blob。完整工程与原生回归以 GitHub 对本提交的运行结果为准。

## 仍未交付

对象树到三维对象的双向选择、真实归档轨道/姿态回放、两种实时仿真数据适配、Windows 实机 GPU/长时性能验收仍不属于本批完成项。场景继续标记为演示数据。

参考：Globe.gl onGlobeReady 与相机 API（https://globe.gl/）；固定版本升级时须重新验证 _destructor 适配。Three.js 资源释放原则（https://threejs.org/manual/en/how-to-dispose-of-objects.html）。
