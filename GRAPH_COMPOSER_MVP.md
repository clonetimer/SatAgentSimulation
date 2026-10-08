# Visual Composer V15：高效建模画布 + 独立结果界面 + 显式观察器工具箱

V15 继续遵循最初目标：**像 Simulink/PyQt 一样快速拖、连、配，直接形成可运行程序；结果分析不侵入普通建模模块。**

核心路径：

```text
快速模板 / 拖模块
  -> simulation_ports 类型化连线
  -> PyQt 风格属性配置
  -> Canonical TaskSpec
  -> 确定性 Python
  -> Run Bundle
  -> 独立结果界面 / 显式 observer sink
```

## 0. V15 建模效率增强

V15 不扩展新的卫星物理模块，重点提升类 Simulink 的编辑效率：

- Shift/Ctrl/Cmd 点击进行多选；
- 在装配画布空白处拖动进行框选；
- 拖动任一已选对象可整体移动；
- 模块与观察器坐标吸附到 20 px 网格；
- 左对齐、顶对齐、水平等间距、垂直等间距；
- 基于注册 signal topology 的自动布局，反馈 SCC 作为同一拓扑组；
- Ctrl/Cmd+A 全选；方向键 20 px 微调，Shift+方向键 5 px 微调；
- Scope / Display / To Workspace 可复制、粘贴、Ctrl/Cmd+D 快速复制；
- 注册物理模块不能复制，因为它们由 parent Capability contract 唯一约束；
- 纯布局变化（移动、自动布局、对齐、框选）不改变 Canonical TaskSpec，也不会让已生成 Python 变成 stale；
- 任务流程模式新增自动布局，拖放位置也吸附到网格。

这些能力只修改工程坐标或 observer 工程元数据，不改变 solver、物理 binding、module selection 或 runtime owner。


## 1. 画布职责

普通 Capability/module 节点只负责建模，不显示时间序列、QoI 徽标或连线 last/peak。完整 QoI、telemetry、events、FMEA、traceability 和 artifacts 统一进入“运行与结果”界面。

运行错误回标继续留在画布，因为它属于建模诊断，而不是结果 Dashboard。

## 2. V14 观察器工具箱

多模块装配提供三个显式、只读、非物理 sink：

| Observer | 用途 | Probe 数量 | 运行时影响 |
|---|---|---:|---|
| `Scope` | 查看时间序列 | 多路 | none |
| `Display` | 查看最近一次 Run 的末值 | 1 路 | none |
| `To Workspace` | 导出已记录 telemetry 为 CSV / JSON | 1 路 | none |

观察器仍保存在兼容字段 `assembly_graph.scopes[]` 中，并新增 `kind`：

```json
{
  "scopes": [
    {
      "kind": "scope",
      "id": "scope-rw",
      "name": "RW Scope",
      "probes": [
        {
          "id": "p1",
          "sourceNode": "module-reaction_wheel",
          "sourcePort": "reaction_wheel.out.wheel_speed_rad_s"
        }
      ]
    },
    {
      "kind": "display",
      "id": "display-rw",
      "name": "RW Display",
      "probes": []
    },
    {
      "kind": "workspace",
      "id": "workspace-rw",
      "name": "RW Export",
      "probes": []
    }
  ]
}
```

V13 工程中没有 `kind` 的旧条目自动按 `scope` 读取，不需要迁移。

## 3. 观察器硬边界

三个 observer 共用同一条可信边界：

- 只能连接正式 `simulation_ports` 的 **output**；
- probe 是虚线观察连接，不是物理 `binding`；
- 不占用物理端口 `fan_out`；
- 不参与 feedback / algebraic-loop 判定；
- 不改变 module selection；
- 不改变 Canonical TaskSpec；
- 不改变 solver、parent adapter 或 recorder 配置；
- 不进入生成的 `run_simulation.py`；
- 只读取 Run Bundle 已经记录的 telemetry；
- `sourceNode` 与 `sourcePort.module_alias` 必须一致，否则拒绝解析。

因此添加、删除、移动、改名 observer 不会使物理 Python 失效。

V14 自动化测试明确比较了“无 observer”和“同时存在 Scope / Display / To Workspace”两种装配：**TaskSpec 完全相同，生成 Python 逐字相同。**

## 4. Scope

使用方式：

1. 添加 `Scope`；
2. 点击模块输出端口；
3. 点击 Scope `signal`；
4. 构建并运行；
5. 在 Scope 检查器中切换解析到的 telemetry series 并查看小型波形；
6. 复杂分析仍跳到独立结果界面。

Scope 可以接多路正式输出。

## 5. Display

`Display` 是单输入数值 sink。运行结束后只显示该输出对应 telemetry 的末值；向量输出可列出解析到的多个分量。

Display 不画曲线，因此适合快速观察 SOC、温度、轮速、误差等当前值，而不会把普通模块变成结果面板。

## 6. To Workspace

`To Workspace` 是单输入数据导出 sink。运行完成后可导出：

- CSV；
- JSON。

对应接口：

```text
POST /visual-composer/export-observer-data
```

请求只包含：

- `run_id`；
- 当前 `assembly_graph`；
- `observer_id`；
- `format=csv|json`。

后端会：

1. 从密封/已生成 Run Bundle 读取 `results/telemetry.jsonl`；
2. 通过注册 assembly contract 验证 observer 的 source node / port 所有权；
3. 只解析该端口对应的 telemetry 列；
4. 流式输出全部已记录行。

因此导出不受工作台 5000 行 telemetry 预览上限影响，也不会把无关 telemetry 列一起导出。测试使用 5105 行 telemetry 验证完整导出。

只有 `kind=workspace` 的 observer 能调用该接口；Scope/Display 请求导出会被拒绝。

## 7. Run overlay

`POST /visual-composer/run-overlay` 仍是只读结果投影接口。V14 保留 `scope_overlays` 兼容字段，同时增加等价别名：

```text
observer_overlays[]
  observer_id
  observer_kind   # scope | display | workspace
  probes[]
    source_port
    source_path
    signals[]
    primary
    resolved
```

后端不会解析或执行任意 Python。

## 8. 程序包

`POST /tasks/export-program` 继续导出：

```text
run_simulation.py
task_spec.json
assembly_graph.json
program_manifest.json
run.sh
run.bat
README.md
```

`assembly_graph.json` 保留 observer 工程信息，方便下次打开工程；`run_simulation.py` 不包含 observer。

manifest 新增/保留：

```text
observer_count
observer_scope_count
observer_display_count
observer_workspace_count
observer_probe_count
observer_runtime_effect = none
```

## 9. 兼容性

V14 不升级物理 assembly schema。observer 仍属于非物理工程注释，现有 `sat-sim.module-assembly.v4` 验证器只处理物理 `nodes / edges / module selections`。

因此：

- V8–V12 没有 observer 的工程正常加载；
- V13 Scope 工程正常加载，并自动视为 `kind=scope`；
- V14 新 observer 不改变旧编译链。

## 10. 当前边界

- Scope 当前是运行后波形，不是实时 streaming scope；
- Display 显示 Run Bundle 末值，不是仿真步内实时刷新；
- To Workspace 只导出已经记录的 telemetry，不会自动修改 recorder；
- 未记录到 Run Bundle 的端口会显示未解析；
- 物理模块仍必须来自 Capability/module registry；
- 不接受任意 Python block 或任意 Basilisk class path。

当前设计原则保持不变：**画布用于建模；结果界面用于分析；只有显式 observer sink 才在模型图上观察数据。**
