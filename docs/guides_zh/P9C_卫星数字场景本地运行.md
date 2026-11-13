# P9-C：卫星数字场景本地运行

## 采用的成熟案例

本阶段采用 **Globe.gl + Three.js** 作为浏览器地球与空间对象渲染层。Globe.gl 官方仓库提供独立的 Satellites 示例，包含地球球面、卫星位置粒子、轨迹路径、相机视角和交互控制。本项目复用其成熟的地球可视化能力，不再自行实现球面投影、轨迹贴地和相机控制基础设施。

- Globe.gl：MIT，固定版本 2.46.2
- three-globe：MIT，固定版本 2.45.6
- Three.js：MIT，固定版本 0.186.1
- 地球纹理：three-globe 包内 Earth Blue Marble 和地形 bump 纹理，随项目离线打包
- 所有 npm 包通过 registry 发布的完整性元数据校验；各包许可证随资源一起分发

上游参考：
- https://github.com/vasturiano/globe.gl
- https://github.com/vasturiano/three-globe
- https://github.com/mrdoob/three.js
- https://vasturiano.github.io/globe.gl/example/satellites/

## 准备离线资源

源码开发环境需要 Node.js 22 和 npm。首次运行或更新资源时，在项目根目录执行：

```powershell
python scripts/vendor_scene_assets.py
```

资源写入 `src/sat_sim/web/vendor/globe/`。运行时不访问 CDN，不依赖 Cesium ion Token，也不请求在线地球影像。

Python wheel 会将 Globe.gl、Three.js、地球纹理和许可证一并打包。安装后的运行环境不需要 Node.js/npm。

## 场景操作

从仿真向导右上方打开“卫星数字场景”：

- 鼠标拖动：旋转地球
- 滚轮：缩放观察距离
- “跟踪卫星”：相机持续跟随演示卫星
- “自由观察”：解除跟踪，手动观察
- “返回全景”：回到全球视角

## 数据真实性边界

当前轨道和卫星位置用于场景渲染技术验证，是明确标注的演示数据，不代表 Basilisk 仿真输出，也不能用于科研分析或效能评估。

后续接入真实仿真时，应由仿真运行记录提供带时间戳的位置、姿态和坐标系定义；渲染层只负责转换与呈现，不自行生成或修改仿真真值。单次仿真结果、两种实时仿真以及历史 Run Bundle 应使用各自明确的数据适配器。

## 内容安全策略

工作台保留原有严格 Content Security Policy。场景使用本地 UMD 与 ESM 静态资源，不要求全站 `unsafe-eval`、`wasm-unsafe-eval` 或宽松 Worker 策略。不要为了场景效果放宽主工作台 CSP。
