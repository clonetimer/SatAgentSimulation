# P9-C 三维场景渲染引擎技术决策

- 日期：2026-11-13
- 决策状态：Three.js 技术验证实现中
- 适用范围：默认工作台的地球、卫星、轨道和相机可视化
- 不改变：Basilisk 物理模型、TaskSpec、Capability Registry、Run Bundle、实时会话 API

## 1. CesiumJS 技术验证结论

CesiumJS 1.147.0 已完成离线运行时 staging，但严格 CSP 下的浏览器验证失败。Chromium 报告 Cesium 主文档执行动态 JavaScript 与 WebAssembly 时违反当前 `script-src 'self' 'unsafe-inline'` 策略。

仅对 Cesium Worker 响应增加 `unsafe-eval` 并不能解决主文档中的限制。要让该版本完整运行，必须扩大主文档的动态代码执行权限。该改动会影响整个仿真工作台，不符合当前安全边界。

因此不采用“全站增加 unsafe-eval/wasm-unsafe-eval”的修复方式，也不把 CSP 回归测试降级为跳过。

## 2. 采用 Three.js 的理由

Three.js 提供本阶段所需的 WebGL 场景能力：
- 球体、纹理、光照和大气层；
- glTF 卫星模型加载；
- 轨道曲线与时变对象位置；
- 相机旋转、缩放、平移与目标跟踪。

Three.js 的静态模块运行链不需要 Cesium 的 Worker bootstrapper，也不要求为主文档开放动态 eval 或 WebAssembly 编译权限。Three.js 与 GLTFLoader、OrbitControls 均使用本地同源静态资源。

NASA Eyes 继续作为任务观察、空间关系和时间交互的产品体验参考；Cesium 的 Natural Earth II 离线影像资源作为低分辨率地球纹理来源，不引入 Cesium 渲染引擎。

## 3. 资源策略

- Three.js 精确固定为 0.187.0，通过 npm 包完整性校验获取；
- Natural Earth II 影像从 CesiumJS 1.147.0 包中仅提取两张 level-0 瓦片；
- 运行时不访问外部 CDN、地图服务或 Cesium ion；
- 自有卫星 glTF 以 CC0-1.0 发布，并标记尺寸放大、非真实结构比例；
- 静态资源随 Python wheel 打包，运行时无需 Node.js。

## 4. 数据真实性边界

本阶段的轨道和卫星位置仍是明确标记的演示数据。三维场景只承担显示与交互，不作为第二套仿真引擎。

后续接入时：
- 离线结果必须来自已封存 Run Bundle；
- 限时实时和持续实时必须来自当前 Basilisk 会话真实状态与遥测；
- 每个样本保留仿真时间、接收时间、序列号和数据新鲜度；
- 无新帧时保留上一帧并标记陈旧，不继续伪造运动。

## 5. 验收

- 严格 CSP 下加载 Three.js、GLTFLoader、OrbitControls；
- 本地 Earth 纹理和 glTF 离线加载；
- Chromium 验证球体、卫星模型、轨道、相机操作和 Viewer 生命周期；
- 主文档 CSP 不含 `unsafe-eval` 或 `wasm-unsafe-eval`；
- Python wheel 包含所有运行资源；
- 既有 P7/P8、P9-A/P9-B 和 Basilisk 回归不退化。
