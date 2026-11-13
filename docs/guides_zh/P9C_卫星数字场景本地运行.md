# P9-C 卫星数字场景本地运行

## 准备本地场景资源

三维场景采用固定版本 Three.js 0.187.0，地球底图采用随包提供的 Natural Earth II 低分辨率影像，卫星采用项目自有 glTF 示意模型。

源码工作区首次运行前，需要 Node.js 22 和 npm：

```powershell
python scripts/vendor_scene_assets.py
```

脚本从 npm registry 获取精确版本的 Three.js 和 CesiumJS 资源包，npm 校验发布的完整性信息；仅复制 Three.js 运行模块、GLTFLoader/OrbitControls 及 Natural Earth II 两张 level-0 地球瓦片。不会把 CesiumJS 运行引擎装入产品，也不依赖 CDN、在线地图或 Cesium ion Token。

准备完成后，按项目原有方式启动 SatAgentSimulation，打开左侧“卫星数字场景”。

## 当前场景内容

- 三维地球球体和本地 Natural Earth II 纹理；
- 大气层效果与星空背景；
- 原创 glTF 卫星示意模型（中心舱体和两侧太阳翼）；
- 黄色虚线演示轨道；
- 卫星跟踪、自由旋转/缩放/平移、返回全景。

卫星模型为可视化示意，尺寸经过放大，不代表真实卫星结构比例。轨道和卫星运动由前端演示时钟驱动，不是 Basilisk 仿真数据。

## 安全边界

场景渲染使用 WebGL，不要求在工作台主文档开放 `unsafe-eval` 或 `wasm-unsafe-eval`。主页面继续使用现有严格 CSP；场景只读取随包静态资源，不读取 Token、任务配置或实时会话凭证。

## 打包

构建 Python wheel 前先运行资源准备脚本。Three.js、Earth 纹理和卫星 glTF 会随 wheel 一并打包，安装后的运行时不需要 Node.js/npm。Node.js 仅用于源码构建阶段准备第三方静态资源。

## 资源与许可

- Three.js：0.187.0，MIT
- Natural Earth II 纹理：来自 CesiumJS 1.147.0 npm 包内置资源；保留包许可证和资源来源记录
- 卫星 glTF：项目原创程序化几何，CC0-1.0
- 运行时外部网络依赖：无
