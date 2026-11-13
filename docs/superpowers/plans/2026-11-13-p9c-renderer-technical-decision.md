# P9-C：卫星数字场景渲染引擎技术决策

- 日期：2026-11-13
- 决策状态：采用 Globe.gl + Three.js 成熟地球渲染组件，进入离线浏览器验收
- 适用范围：默认工作台的地球、卫星、轨道和相机可视化
- 不改变：Basilisk 物理模型、TaskSpec、Capability Registry、Run Bundle、实时会话 API

## 1. CesiumJS 技术验证结论

CesiumJS 1.147.0 已完成离线运行时 staging，但严格 CSP 下的 Chromium 浏览器验证失败。实际报错涉及动态 JavaScript 执行和 WebAssembly 编译，不是简单的静态资源缺失。

仅对 Worker 响应增加 `unsafe-eval` 并不能解决主文档的执行限制。为通过该版本的完整运行链，需要扩大主文档动态代码执行权限，这会影响整个仿真工作台的安全边界。

因此不采用全站增加 `unsafe-eval`/`wasm-unsafe-eval` 的修复方式，也不降低 CSP 回归标准。

## 2. 成熟案例评估与最终选择

评估了三类路线：

1. **CesiumJS**：高精度 WGS84 地球、地形/影像和大规模 3D Tiles 能力最完整，但当前版本的浏览器执行链与工作台严格 CSP 存在实测冲突。
2. **NASA Web WorldWind**：NASA/ESA 长期地理可视化 SDK，适合地理数据、地形和 GIS 图层；其上游文档仍以较旧的 WebGL/构建体系为主，且默认示例会请求远程图像服务。
3. **Globe.gl + three-globe + Three.js**：上游提供成熟的 Satellites 示例，内置球面纹理、路径层、三维对象层、坐标转换、轨道路径和 OrbitControls 交互。适合当前“全球地球背景 + 轨道 + 卫星 + 相机操作”的工作台场景。

最终选择第三条路线。它能直接复用成熟 Satellites 示例的架构，保持较轻的本地资源闭包，并在当前浏览器中保留严格 CSP。该选择不意味着 Globe.gl 可替代 Cesium 的高精度地理分析、3D Tiles、地形和大规模 GIS 能力；若未来需求升级，应单独重新评估 Cesium 或 WorldWind 的安全部署方式。

## 3. 固定依赖与资源策略

- Globe.gl 2.46.2，MIT；
- three-globe 2.45.6，MIT；
- Three.js 0.186.1，MIT；
- Earth Blue Marble 和地形 bump 纹理来自 three-globe npm 包内示例资源；
- 通过 npm registry 发布的 integrity 元数据校验固定版本 tarball；
- 将 UMD、Three.js ESM 核心模块、纹理和许可证随 Python wheel 打包；
- 运行时不访问 CDN、在线地图服务或 Cesium ion；
- 不启用 `unsafe-eval`、`wasm-unsafe-eval` 或额外 Worker CSP 例外。

## 4. 当前场景模型与数据真实性

场景使用 Globe.gl 的 paths layer 绘制演示轨道，objects layer 挂载原创 Three.js 组合卫星模型。当前轨道周期、倾角和卫星位置仅用于视觉与交互验收，明确标记为演示数据，不代表 Basilisk 仿真结果。

后续接入真实数据时：
- 离线结果必须来自已封存 Run Bundle；
- 限时实时与持续实时必须来自当前 Basilisk 会话真实状态与遥测；
- 每个样本保留仿真时间、接收时间、序列号、坐标系和数据新鲜度；
- 无新帧时保留上一帧并标记陈旧，不继续伪造运动；
- 场景层只负责坐标转换和呈现，不得成为第二套仿真引擎。

## 5. 验收标准

- Globe.gl UMD、Three.js ESM 与地球纹理全部由本地同源资源加载；
- Chromium 验证地球、路径、三维卫星对象和相机控制；
- 浏览器网络记录不出现外部请求；
- 主文档 CSP 不含 `unsafe-eval` 或 `wasm-unsafe-eval`；
- Viewer 离开与再次进入时正确释放/重建渲染资源；
- Python wheel 包含全部运行时资源与许可证；
- 既有 P7/P8、P9-A/P9-B、单次仿真和 Basilisk 原生回归不退化。

## 6. 上游参考

- Globe.gl：<https://github.com/vasturiano/globe.gl>
- three-globe：<https://github.com/vasturiano/three-globe>
- Three.js：<https://github.com/mrdoob/three.js>
- Globe.gl Satellites 示例：<https://vasturiano.github.io/globe.gl/example/satellites/>
- NASA Web WorldWind：<https://github.com/NASAWorldWind/WebWorldWind>
