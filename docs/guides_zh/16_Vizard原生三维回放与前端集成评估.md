# Vizard 官方三维可视化集成评估与原生回放 P0

状态：**P0 仅评估 + 原生有限时长录制**；浏览器内运行 Vizard、持续整星在线推流、启动 Unity 桌面程序和一键 Web 下载尚**未实现**。不能将本文件作为任何这些能力的验收证明。

## 官方能力与保真度

- AVSLab Basilisk 的 `vizInterface` C++ 模块读取 **同一个 Basilisk SimulationBaseClass 任务** 的 Spacecraft 等消息，经 `Basilisk.utilities.vizSupport.enableUnityVisualization(..., saveFile=...)` 产生 Vizard 原生 `.bin` 二进制轨迹。
- Vizard 是 Unity 桌面三维查看器（Windows/Linux/macOS），可回放 `.bin`；官方亦支持 ZeroMQ 实时及双向通信，2026 年 Vizard 2.4.0 已公开 Unity 源码（ISC 许可；发布的二进制另有署名条款，应在再分发前复核）。
- 此方法不是从稀疏的遥测曲线凭空插值姿态，也没有用 JavaScript 模拟物理。支持空间轨迹、真实动力学姿态、轮组显示；物理原生性取决于接入的 Basilisk 模块和场景，项目热网络仍不是 Basilisk 官方热模型。
- 本仓库的太阳、行星历表仍是现有模型的简化设定；Vizard 的美观程度不等于轨道／传感器／热控计算精度，也不会解决断点续跑。

参考：
- <https://avslab.github.io/basilisk/Vizard/Vizard.html>
- <https://avslab.github.io/basilisk/Vizard/vizardAdvanced/vizardSettings.html>
- <https://avslab.github.io/basilisk/examples/scenarioBasicOrbitStream.html>
- <https://github.com/AVSLab/vizard>

## 可行性矩阵

| 方案 | 评估 | 下一步 |
|---|---|---|
| 原生有限时长整星运行内录制 `.bin` | **高，P0 实施** | `vizSupport` 创建真实 vizInterface，先在 CI 验证与基线物理轨迹相等，再生成哈希清单 |
| 在运行结果中提供「下载 Vizard 回放」 | **高，但未实现** | 只允许服务端 Run Bundle 自有目录，成功生成后密封写入 artefact index；提供同源按 run_id 下载，不暴露任意路径 |
| 持续仿真发送 Vizard 实时数据 | **可行，尚未实施** | ZeroMQ `liveStream/broadcastStream` 在独立进程/隔离端口、明确连接权限和超时控制；绝不因缺少桌面 Viewer 阻塞 TC/TM |
| 将原版 Vizard 直接作为网页 iframe | **不具备现成的公开官方嵌入接口** | Unity 官方工程主要提供桌面平台构建；如需原版嵌入，须评估 Unity WebGL 移植与网络、文件、线程、着色器、授权兼容性并提交独立工程验证 |
| 在 SAT 网页绘制轻量三维预览 | **技术上可行，非原版 Vizard** | 从真实 Basilisk 位置/姿态原始记录构建只读轨迹，以 Three.js/WebGL 渲染；与 Vizard 官方回放明确区别 |

## P0 本地原生回放（首批功能）

本项目用真实 `Spacecraft` / `ReactionWheelStateEffector`，在有限时长原生 Task 内追加 `vizInterface`：**不改变原有求解器**。接入是显式 opt-in，旧 TaskSpec 的计算图不变；不开启 socket live-stream，不允许 HTTP 用户直接传入目标磁盘路径。仅受信任的本地 CLI 入口创建新文件。

Windows Git Bash（已有 Basilisk 环境）：

```bash
PYTHONPATH=src:. python scripts/export_native_vizard.py --duration-s 120 --output reports/vizard/native-demo.bin
```

Windows PowerShell：

```powershell
$env:PYTHONPATH="src;."
python scripts/export_native_vizard.py --duration-s 120 --output reports/vizard/native-demo.bin
```

也可选 `--task-spec <本地有限时长整星 TaskSpec JSON>`，但必须是 `whole_spacecraft.unified_native.v1`、Basilisk backend、`continuous=false`、仿真时间不超过 3600 秒。输出包括官方 vizInterface `native-demo.bin` 及同目录 `native-demo.vizard.json`（格式、SHA-256、字节数、仿真时长、真实性声明）。指定文件已存在则拒绝覆盖。

查看：安装官方 [Vizard 桌面版](https://github.com/AVSLab/vizard)，用 Vizard 启动面板选择生成的 `.bin` 文件。**不是浏览器内直接播放**。

P0 真实验收：官方 `vizSupport.vizFound`、真实 `.bin` 的非空文件与 SHA-256、官方 vizInterface 注册和 RW 连接、与未录制整星的关键位置/指向误差/轮速 **A/B 数值一致性**；伪造微小文件必须拒绝；禁止持续运行使用该有限录制入口。验证文件实际可由特定版本 Vizard 桌面软件打开仍需要有桌面程序的现场手动验收，CI 不能冒充 Unity 图形运行证明。

## 前端迭代计划

1. **P1**：将录制任务与 Run Bundle 同生命周期绑定（文件命名、路径规范化、上限、校验、SHA-256、终止/超时清理）；结果页出现「下载 Vizard .bin」并提供桌面版安装指引。只对正式完成的有限仿真显示按钮。
2. **P2**：持续整星独立 ZeroMQ broadcast，只读从属 Viewer，默认关闭；鉴权、端口隔离、超时、缓冲上限、断线继续仿真与 TC/TM 不阻塞是上线门槛；不把 Vizard 的回传输入直接提升为卫星遥控。
3. **P3**：若非程序员用户要求“不装桌面软件、浏览器即可看三维”，用真实轨迹/姿态消息做轻量网页 3D，配合遥测曲线时间游标。不要把自研 WebGL 视图标成“Vizard 原版”。

## 安全／许可／工程边界

- Vizard 桌面程序无需作为 Python/Basilisk 依赖打包，也不能隐式下载或安装；与 SAT 本体分离。不要提交 Unity 资产或模型到 SAT 仓库，分发前单独核验上游二进制许可证和资产来源。
- 原生 `.bin` 可能包含航天器构型、轨迹、故障与任务敏感数据，应作为 Run Bundle 制品加权限控制；不可把输出文件系统路径暴露为远程 API 参数。
- Vizard 可视化不能验证整星跨进程恢复等价性。目前恢复仍为 BLOCKED；依赖安全 Issue #39 也与 Vizard 录制验收独立。
