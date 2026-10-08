# SAT 卫星仿真平台 · Visual Composer V15

> **完全新手？先不要从本 README 往下硬读。** 直接打开 [`START_HERE.md`](START_HERE.md)，然后按 [`docs/tutorials_zh/README.md`](docs/tutorials_zh/README.md) 的顺序做练习。教程从“启动平台并完成第一次运行”开始，不要求先理解 Capability / TaskSpec。

> **建议状态：冻结为当前稳定工程基线。**  
> V15 已形成“图形化建模 → 校验 → 生成可执行 Python → 运行 → 独立结果分析 / Scope 观察 → 导出可运行程序”的完整闭环。现阶段建议先用于真实任务，不再主动堆功能；后续只根据实际使用中暴露的问题做修复或针对性增强。

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

## 2. 当前适用范围

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

### 4.1 已经有原 SAT 项目环境：推荐

这是最稳妥的方式。

1. 解压 V15 到一个新的项目目录，例如：

```text
sat-platform-visual-composer-v15/
```

2. 保留原项目作为备份，并继续使用原项目已经验证过的 `.venv` / wheelhouse。

3. 在 V15 根目录重新做 editable install，使虚拟环境指向 V15 源码。

Linux/macOS 示例：

```bash
cd /path/to/sat-platform-visual-composer-v15
source /path/to/original-sat/.venv/bin/activate
python -m pip install --no-deps -e .
python -m sat_sim.agent_cli doctor --require-api --strict-assets
```

启动：

```bash
VENV_DIR=/path/to/original-sat/.venv ./scripts/start_local.sh
```

浏览器打开：

```text
http://127.0.0.1:8000/
```

Windows PowerShell 示例：

```powershell
cd C:\path\to\sat-platform-visual-composer-v15
& C:\path\to\original-sat\.venv\Scripts\Activate.ps1
python -m pip install --no-deps -e .
python -m sat_sim.agent_cli doctor --require-api --strict-assets

$env:VENV_DIR = "C:\path\to\original-sat\.venv"
.\scripts\start_local_windows.ps1
```

> 当前正式部署目标仍优先是 Ubuntu/Linux。Windows 脚本可用于开发和兼容验证。

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

V15 源码包本身不包含完整离线 wheelhouse。若从零安装，请准备与 `requirements-lock.txt` 匹配的依赖，尤其是 Basilisk runtime。

推荐先检查当前 Python：

```bash
python --version
```

支持范围：

```text
Python >=3.10,<3.14
```

然后创建环境并安装你单位/原项目保存的依赖。安装完成后，在 V15 根目录执行：

```bash
python -m pip install --no-deps -e .
python -m sat_sim.agent_cli doctor --require-api --strict-assets
```

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
