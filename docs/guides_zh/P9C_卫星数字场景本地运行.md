# P9-C 卫星数字场景本地运行

## 准备 Cesium 离线资源

卫星数字场景使用固定版本 CesiumJS 1.147.0。运行时不从 CDN 或 Cesium ion 下载地球底图、脚本或 Worker。

首次在源码工作区运行前，需要 Node.js 22 和 npm：

```powershell
python scripts/vendor_cesium_assets.py
```

脚本通过 npm registry 获取精确版本的预构建包，npm 校验包发布的完整性信息，只复制 `Build/Cesium` 运行时文件和 Apache-2.0 许可证到：

```
src/sat_sim/web/vendor/cesium/
```

准备完成后，按项目原有方式启动 SatAgentSimulation，打开左侧“卫星数字场景”。

## 场景内容边界

当前技术验证包含：
- 本地 Natural Earth II 地球影像；
- 地球大气和光照；
- 一条黄色虚线演示轨道；
- 一个示意卫星标记及轨迹；
- 跟踪卫星、自由观察和返回全景。

此阶段轨道和卫星运动是演示数据，不是 Basilisk 输出，不得用于科学分析、任务评估或故障诊断。真实离线运行结果和两种实时仿真联动属于后续阶段。

## 打包

构建 Python wheel 前先执行资源准备脚本。Cesium 静态资源会随 wheel 一并打包，安装后的运行时无需 Node.js/npm。Node.js 仅用于源码构建阶段准备第三方静态资源。

## 资源与许可

- CesiumJS：1.147.0
- License：Apache-2.0
- 地球底图：CesiumJS 包内 Natural Earth II 低分辨率资源
- 外部在线底图：默认不启用
- 运行时 Token：不需要
