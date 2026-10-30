# SAT 卫星仿真平台 · Visual Composer V15

> **完全新手？先不要从本 README 往下硬读。** 直接打开 [`START_HERE.md`](START_HERE.md)，然后按 [`docs/tutorials_zh/README.md`](docs/tutorials_zh/README.md) 的顺序做练习。教程从“启动平台并完成第一次运行”开始，不要求先理解 Capability / TaskSpec。

使用 uv 安装当前开发环境：从仓库根目录运行 `python scripts/setup_uv.py`。已有完整缓存与资产时可加 `--offline`。逐项判定见[当前需求验收状态](reports/current_requirements_status.md)，过程及失败记录见[功能补齐记录](docs/functional_completion.md)；实际目标卫星的参数和遥测标定仍需对应工程数据。

没有现成模型服务时，可按 [ModelScope 本地微调与脚本生成](docs/modelscope_local_training.md) 复现独立 GPU 环境、基座来源、训练和真实模型评测。本地 v5 已实际通过热环境、导热参数、电池容量损失、脚本创建／修改及网页下载执行链路。

最新进展见 [2026-10-30 检查点](reports/progress_checkpoint/README.md)：冻结版本完整回归 **1978通过、0失败、5跳过、4警告**；模型网页85项、原生曲线76项、独立851项及45张实际看图复核通过。新的4 W/K配置实际24小时耐久已从零启动，尚未取得终态资格，目标仍为进行中。

该实例现已观测超过2小时30分钟，历史最大漂移约1.92秒，状态为`DEGRADED`。前8400仿真秒的33602条遥测记录通过链路、采样、八节点温限和SOC复核；这不是24小时终态或全步严格实时资格，见[本轮记录](reports/progress_checkpoint/r11-2h30m-evidence-manifest.json)。此前[1小时记录](reports/progress_checkpoint/r11-1h-endurance-observation.json)中的约1.57秒峰值按原样保留。

该实例现已观测超过2小时30分钟，历史最大漂移约1.92秒，状态为`DEGRADED`。前8400仿真秒的33602条遥测记录通过链路、采样、八节点温限和SOC复核；这不是24小时终态或全步严格实时资格，见[本轮记录](reports/progress_checkpoint/r11-2h30m-evidence-manifest.json)。此前[1小时记录](reports/progress_checkpoint/r11-1h-endurance-observation.json)中的约1.57秒峰值按原样保留。

> **当前资格：本轮生产功能和可视化验收通过；实际24小时耐久及其终态复核仍在进行。历史分数与失败证据保留。**
>
> V15 已形成“图形化建模 → 校验 → 生成可执行 Python → 运行 → 独立结果分析 / Scope 观察 → 导出可运行程序”的工程流程。当前适用于内部仿真与算法试验；用于具体卫星任务前，仍需核验对应参数、工况及实测残差。

## 1. V15 是什么

V15 是 SAT 卫星仿真平台的图形化建模版本。它保留原有 Capability Registry、Canonical TaskSpec、Validator、Execution Planner、注册 Runner 和 Run Bundle 执行链，在其上增加了类似 Simulink / PyQt 属性编辑器的 Visual Composer。

典型流程：

```text
拖模块 / 选模板
    ↓
连线 / 自动匹配
    ↓
属性面板修改参数
    ↓
校验图或装配
    ↓
生成确定性 Python
    ↓
构建并运行
    ↓
运行与结果界面 / Scope / Display / To Workspace
```

V15 的建模画布只负责建模。普通物理模块不会叠加曲线或大量结果信息；完整结果统一在“运行与结果”界面查看。需要在模型附近观察信号时，显式放置 `Scope`、`Display` 或 `To Workspace`。

## 持续卫星仿真（新增，可选）

实时会话可选 `continuous: true`，将同一个 Basilisk 整星实例按量子持续推进并保持遥控、遥测接口可用，直到显式停止；不是遥测循环播放。此模式仍需长期运行与恢复验收，不影响原有限时任务。参见 [持续卫星仿真指南](docs/guides_zh/13_持续卫星仿真指南.md)。

> 历史集成边界参见 [持续卫星仿真 Stable 集成验收](docs/guides_zh/14_持续卫星仿真Stable集成验收.md)。当前不支持跨进程整星状态续算；本轮按“异常中止后保留证据、重新创建场景”验收。历史 R9-K 定制电池试验不构成当前整星自动恢复支持。当前长测及模型验收以[功能补齐记录](docs/functional_completion.md)为准。

## 2. 当前适用范围

推荐执行能力如下；这些标识对应后端注册表，页面按对象名称展示：

| 仿真对象 | Capability ID |
| --- | --- |
| 整星 | `whole_spacecraft.unified_native.v1` |
| 姿态控制分系统 | `subsystem.adcs_unified_native.v1` |
| 电源分系统 | `subsystem.eps.unified_native.v1` |
| 通信数据分系统 | `subsystem.comm_data.unified_native.v1` |
| 推进分系统 | `subsystem.propulsion.unified_native.v1` |

V15 适合作为：

- 内部工程仿真与算法验证；
- 图形化搭建已注册的卫星仿真任务；
- 受约束多模块装配；
- 生成和导出可运行 Python 项目；
- 运行结果、遥测、QoI 和事件分析；
- 少量参数快速扫描；
- 模型对比、错误定位和回归测试。

V15 **不是**飞行软件、HIL 平台或认证级数字孪生。Basilisk 原生执行和当前模型库仍属于工程仿真边界。

## 3. 包含什么，不包含什么

`Visual Composer V15` ZIP 是**完整源码树**，但不重复打包大型离线依赖 wheel。

因此通常会看到：

- V15 源码 ZIP：约 5 MiB；
- Basilisk wheel：单独约 80+ MiB；
- Pillow、SGP4、FastAPI 等依赖：单独安装或放在 wheelhouse。

不要用源码 ZIP 的大小判断 Basilisk runtime 是否存在。

如果你已经有原 SAT 项目的 Python 环境和离线 wheel，**推荐继续复用原环境**，不要重复安装一套。

## 4. 推荐安装方式

### 4.1 使用 uv 创建或更新当前环境：推荐

确保 `uv` 在 PATH 中，然后从仓库根目录执行：

```powershell
uv --version
python scripts/setup_uv.py
.\scripts\start_local_windows.ps1
```

安装入口会在需要时创建 Python 3.12 的 `.venv`，核验固定官方 Basilisk wheel 的来源，构建仅修改发行元数据的 `satfix2` 变体，通过 uv 安装锁定依赖，获取环境资产，并执行依赖冲突与严格 doctor 检查。现有 `.venv` 会按当前锁定版本更新；旧环境的通过记录不作为更新后的验收结果。

已有完整 uv 缓存、固定官方 wheel 和环境资产时，可以离线安装：

```powershell
python scripts/setup_uv.py --offline
```

缺失缓存或资产时，离线命令会失败并报告缺项。当前默认环境采用 Pillow 13 和 metadata-only `bsk==2.11.0+satfix2`；Basilisk 仿真代码、原生库和数据载荷与固定官方发行件相同。旧 `satfix1` 制品保留供历史对照。

浏览器打开 `http://127.0.0.1:8000/`。Linux x86_64 可使用同一 uv 安装入口，再运行 `./scripts/start_local.sh`；本轮 Windows 安装与实际原生运行已实测，Linux 部署资格仍待独立复验。当前预编译安装入口不接受 macOS 或其他架构。

### 4.2 直接升级原 source-only 工程

如果你不想创建新目录，可使用随 V15 提供的累计补丁：

```bash
cd /path/to/your/original-sat-project
patch -p1 < sat-platform-original-to-v15.patch
```

或者使用 `sat-platform-original-to-v15-overlay.zip`，把 `overlay/` 内的文件按相对路径覆盖到原项目根目录。

如果原项目中已有你自己的修改，请先创建 Git 分支或完整备份，再应用补丁。

详细说明见：

```text
UPGRADE_FROM_ORIGINAL.md
```

### 4.3 从零安装

按 4.1 使用 uv 安装。首次安装需要网络以获取固定依赖和环境资产；源码包本身不包含完整离线 wheelhouse。仅安装源码而不更新依赖，不能证明满足当前锁定版本或安全检查。

Basilisk 可单独检查：

```bash
python scripts/ensure_basilisk_runtime.py --check-only
```

如果你有本地 `bsk-2.11.0*.whl`：

```bash
python scripts/ensure_basilisk_runtime.py --wheel /path/to/bsk-2.11.0.whl
```

> `scripts/bootstrap_local.sh` / `bootstrap_windows.ps1` 面向带 `third_party/wheels` 与 `constraints.txt` 的完整部署包。如果你使用的是 source-only V15 ZIP，请优先复用原环境或自行提供对应 wheelhouse。

## 5. 启动工作台

Linux/macOS：

```bash
./scripts/start_local.sh
```

Windows：

```powershell
.\scripts\start_local_windows.ps1
```

默认地址：

```text
http://127.0.0.1:8000/
```

停止服务：前台终端按 `Ctrl+C`。

默认运行数据目录：

```text
runs/
```

控制面和 API 临时制品：

```text
.sat_sim_api/
```

## 6. 第一次使用：推荐直接走“图形组装”

打开工作台后，顶部选择：

```text
图形组装
```

Visual Composer 有两个模式：

1. **任务流程**：适合单个 Capability + 参数 / 事件 / 输出 / 代码 / 运行流程；
2. **多模块装配 · V15**：适合类似 Simulink 的模块装配和受约束模块替换。

如果只是第一次体验，优先使用“多模块装配”的**快速开始模板**。

---

# 7. 任务流程模式

## 7.1 建一个最简单任务

进入：

```text
图形组装 → 任务流程
```

推荐操作：

1. 在左侧“执行能力”搜索 Capability；
2. 将能力拖到画布；
3. 点击“恢复基础流程”或“自动布线”；
4. 画布形成：

```text
模型 → 参数配置 → 事件注入 → 输出选择 → Python 代码 → 运行器
```

5. 点击节点，在右侧“节点属性”修改参数；
6. 点击“校验图”；
7. 点击“生成 Python”查看确定性脚本；
8. 点击“构建并运行”。

## 7.2 工程保存

任务流程支持：

- 保存浏览器草稿；
- 加载浏览器草稿；
- 导出 `.satgraph.json`；
- 导入 `.satgraph.json`。

工程 JSON 保存图、Capability 和表单参数，不保存任意用户 Python。

## 7.3 撤销 / 重做

```text
Ctrl/Cmd + Z             撤销
Ctrl/Cmd + Shift + Z     重做
Ctrl/Cmd + Y             重做
Esc                      取消当前接线
```

---

# 8. 多模块装配模式

进入：

```text
图形组装 → 多模块装配 · V15
```

## 8.1 最快方式：快速开始

“快速开始”区域提供已经验证过的装配模板。

推荐流程：

1. 选择快速模板；
2. 系统自动加载注册模块和正式 signal binding；
3. 如需替换模块，从“模块库”拖入兼容模块；
4. 模块会自动匹配合法 slot；
5. 点击“校验装配”；
6. 点击“构建并运行”。

模块不能随意拼接。后端会检查：

- payload schema；
- dtype / shape；
- unit；
- timing；
- direct-feedthrough；
- fan-in / fan-out；
- module interface；
- allow-list；
- solver / feedback policy。

这意味着界面像 Simulink，但执行边界仍由 SAT Capability contract 控制。

## 8.2 拖入模块

模块库默认只显示当前装配可用模块。

可以：

- 搜索模块、接口或领域；
- 把模块拖到对应 slot；
- 也可以直接拖到画布空白处，系统自动寻找最近的兼容 slot。

如果不兼容，后端会拒绝，不会生成“看起来连上了但实际不能运行”的模型。

## 8.3 参数修改

选择模块后，右侧检查器可以修改当前允许暴露的运行参数，例如：

- 仿真时长；
- 积分步长；
- 输出采样周期；
- Capability Schema 注册的模型参数。

参数变化后重新执行“构建并运行”即可。

---

# 9. V15 建模效率操作

## 9.1 多选与框选

- `Shift / Ctrl / Cmd + 点击`：追加/取消选择；
- 在画布空白处拖动：框选；
- `Ctrl/Cmd + A`：全选；
- 拖动任一已选对象：整体移动。

移动默认吸附到 20 px 网格。

## 9.2 自动布局与对齐

工具栏提供：

- 自动布局；
- 左对齐；
- 顶对齐；
- 水平等距；
- 垂直等距。

这些操作**只改变画布坐标**，不会使已生成的 Python 过期。

## 9.3 键盘微调

选中对象后：

```text
方向键           移动 20 px
Shift + 方向键   移动 5 px
```

## 9.4 复制观察器

为了保护物理拓扑，V15 不允许任意复制 EPS、Thermal、Reaction Wheel 等物理 slot。

可以复制的是显式观察器：

```text
Ctrl/Cmd + C     复制选中的 Scope / Display / To Workspace
Ctrl/Cmd + V     粘贴
Ctrl/Cmd + D     复制一份
Delete           删除选中的观察器
```

---

# 10. Scope / Display / To Workspace

普通模块自身不显示曲线。需要局部观察时，在模块库上方加入显式观察器。

## 10.1 Scope

用途：查看一个或多个信号的时间序列。

操作：

1. 点击“Scope”；
2. 点击物理模块的输出端口；
3. 点击 Scope 的输入端口；
4. 构建并运行；
5. 选中 Scope，在右侧检查器切换和查看时间序列。

Scope 是只读 observer：

- 不进入求解器；
- 不占用物理 fan-out；
- 不改变 TaskSpec；
- 不改变生成 Python。

## 10.2 Display

用途：只显示最近一次运行的单路信号末值。

适合快速观察：

- SOC；
- 温度；
- 轮速；
- 姿态误差；
- 功率等标量/展开后信号。

## 10.3 To Workspace

用途：将所接信号导出为 CSV / JSON。

它直接从 Run Bundle 已记录的 telemetry 中导出完整列，不依赖界面预览缓存。

使用前先完成一次运行，然后选择 To Workspace，在检查器中执行导出。

---

# 11. 生成代码和运行

## 11.1 校验

任务流程：

```text
校验图
```

多模块装配：

```text
校验装配
```

推荐在生成代码前先校验。校验失败时先修正红色/错误节点、端口或 binding。

## 11.2 生成 Python

点击：

```text
生成 Python
```

代码由平台的 deterministic script exporter 生成，不是把画布直接拼成任意 Python。

只移动/对齐节点不会使代码过期；真正修改参数、模块选择或物理拓扑后才需要重新生成。

## 11.3 构建并运行

点击：

```text
构建并运行
```

工作台会执行：

```text
Graph / Assembly
  → 校验
  → Canonical TaskSpec
  → Execution Plan
  → Registered Runner
  → Run Bundle
```

完成后点击：

```text
查看运行结果
```

进入独立结果界面。

---

# 12. 结果在哪里看

完整结果不堆在建模画布上。

“运行与结果”区域提供：

- QoI / metrics；
- telemetry 曲线；
- events；
- validation；
- FMEA / traceability；
- Run Bundle artifacts；
- 数据集下载；
- HTML 报告（若该运行生成）。

因此推荐职责划分：

```text
建模画布             负责建模
运行与结果界面       负责完整分析
Scope                 负责局部波形观察
Display               负责局部末值查看
To Workspace          负责数据导出
```

---

# 13. 快速调参

任务流程和多模块装配均提供：

```text
快速调参
```

当前约束：

- 同时选择 1–3 个注册数值参数；
- 每个参数 2–5 个候选值；
- 总组合数最多 12；
- 选择一个注册 QoI 作为目标；
- 支持 minimize / maximize。

系统会自动创建多个真实 Run Bundle，按目标 QoI 排序。

扫描后点击：

```text
应用最优参数
```

再重新生成代码和运行即可。

不要把快速调参当成通用优化器；它适合少量参数的工程快速搜索。

---

# 14. Baseline / Replacement 对比

多模块装配支持：

```text
与基线对比
```

平台会：

1. 运行当前 replacement 装配；
2. 恢复 baseline module selection；
3. 运行 baseline；
4. 对共同 QoI 做差值比较。

适合检查模块替换是否改变关键指标。

---

# 15. 导出可运行程序包

点击：

```text
导出可运行程序包
```

会得到 ZIP，例如：

```text
my_simulation_program/
├── run_simulation.py
├── task_spec.json
├── visual_graph.json 或 assembly_graph.json
├── program_manifest.json
├── run.sh
├── run.bat
└── README.md
```

Linux/macOS 推荐：

```bash
bash run.sh
```

Windows：

```bat
run.bat
```

也可以：

```bash
python run_simulation.py
```

> 导出的程序包包含模型与入口代码，但不会打包整个 Python/Basilisk runtime。运行机器仍需安装匹配的 SAT 平台环境。

---

# 16. 推荐的日常工作方式

建议把 V15 当成当前稳定基线，日常按下面流程使用：

```text
1. 从快速模板起步
2. 拖入需要的兼容模块
3. 自动布局 / 整理画布
4. 修改参数
5. 需要时添加 Scope / Display
6. 校验装配
7. 生成 Python
8. 构建并运行
9. 到独立结果界面看完整结果
10. 必要时快速调参或 baseline 对比
11. 稳定后导出可运行程序包
```

现阶段不建议继续为了功能数量持续升级版本。更好的方式是先用 V15 完成真实模型任务，记录：

- 哪些操作仍然慢；
- 哪些模块还无法装配；
- 哪些参数暴露不合理；
- 哪些错误提示不够清楚；
- 哪些结果缺 telemetry；
- 哪些工作流需要重复人工操作。

下一版本只解决真实使用中排名最高的问题。

---

# 17. 常见问题

## Q1：为什么 V15 ZIP 比“整个 SAT 环境”小很多？

因为 V15 是源码树，不包含你单独保存的 Basilisk、Pillow 等大型 wheel。Basilisk 一个 wheel 本身就可能超过 80 MiB。

## Q2：应该把 V15 整个目录放进旧 SAT 项目吗？

不要嵌套。推荐二选一：

- 把 V15 解压成新的项目根目录，复用旧 `.venv`；
- 在旧项目根目录应用 `sat-platform-original-to-v15.patch` / overlay。

## Q3：拖动节点后为什么不需要重新生成 Python？

因为节点坐标只是编辑器布局，不属于 TaskSpec。只有参数、模块选择、物理连接等语义变化才会令代码过期。

## Q4：Scope 会改变仿真吗？

不会。Scope / Display / To Workspace 是只读 observer，不进入物理求解链。

## Q5：为什么物理模块不能随便复制？

V15 的多模块装配仍由正式 Capability contract 约束。很多物理模块对应唯一 slot，随意复制会产生无定义的物理拓扑，因此编辑器只允许复制观察器。

## Q6：为什么一个模块拖不上去？

通常是 interface、payload、unit、timing、fan-in/fan-out、allow-list 或 solver policy 不兼容。查看右侧检查器和装配状态提示。

## Q7：运行失败先看哪里？

优先：

1. 画布错误高亮；
2. 工作台通知；
3. 运行与结果 → Validation / Events；
4. `runs/<run_id>/` Run Bundle；
5. 系统诊断。

---

# 18. 自检和测试

环境诊断：

```bash
python -m sat_sim.agent_cli doctor --require-api --strict-assets
```

Basilisk 检查：

```bash
python scripts/ensure_basilisk_runtime.py --check-only
```

工作台核心测试：

```bash
pytest -q tests/test_web_workbench.py
```

Visual Composer 相关测试：

```bash
pytest -q \
  tests/test_visual_graph.py \
  tests/test_assembly_graph.py \
  tests/test_visual_diagnostics.py \
  tests/test_visual_results.py \
  tests/test_visual_tuning.py \
  tests/test_web_workbench.py
```

完整测试：

```bash
pytest -q
```

---

# 19. 进一步文档

推荐阅读顺序：

1. `README.md` —— 日常使用入口；
2. `GRAPH_COMPOSER_MVP.md` —— Visual Composer 架构、边界与版本演进；
3. `UPGRADE_FROM_ORIGINAL.md` —— 从原 SAT 工程升级；
4. `docs/guides_zh/01_快速开始.md` —— 平台通用启动；
5. `docs/guides_zh/02_工作台使用指南.md` —— 工作台功能；
6. `docs/TaskSpec规范.md` —— Canonical TaskSpec；
7. `docs/Agent能力边界说明.md` —— Agent / 执行边界。

---

## V15 使用原则

**把 V15 当成当前稳定工程版本使用，不再为了“继续推进”而继续推进。**

优先完成真实卫星仿真任务；只有实际使用反馈证明某个问题值得解决时，再进入下一轮开发。
