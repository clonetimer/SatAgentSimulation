# 05｜Scope / Display / To Workspace

## 本课目标

学习 V15 中正确的结果观察方式：

```text
完整分析 → 结果页
局部曲线 → Scope
局部末值 → Display
导出数据 → To Workspace
```

普通物理模块本身不画曲线。

---

# 1. Scope：看波形

以 ADCS + Reaction Wheel 为例。

## 操作步骤

1. 加载 `ADCS + Reaction Wheel · 可运行组合`；
2. 添加一个 `Scope`；
3. 点击 Reaction Wheel 的 output port；
4. 再点击 Scope 的 `signal` 输入；
5. 你会看到一条虚线 observer probe；
6. 构建并运行；
7. 选中 Scope；
8. 在右侧检查器选择解析到的 telemetry series。

例如 Reaction Wheel 的向量轮速输出可能解析为：

```text
adcs.rw.speed_rad_s_0
adcs.rw.speed_rad_s_1
adcs.rw.speed_rad_s_2
```

Scope 可以在这几个分量间切换。

---

# 2. Scope 为什么是虚线

因为它不是物理 binding。

Scope：

- 不进入 solver；
- 不改变 TaskSpec；
- 不占物理 fan-out；
- 不影响反馈环；
- 不进入生成的 runner。

所以可以放心给同一信号接多个观察器。

---

# 3. Display：看末值

如果你只想知道：

```text
最后 SOC 是多少？
最后温度是多少？
最后轮速是多少？
```

用 Display 比 Scope 更简单。

步骤：

1. 添加 Display；
2. 连接一个 output；
3. 运行；
4. 选中 Display；
5. 查看最近一次 Run 的末值。

Display 是单输入 observer。

---

# 4. To Workspace：导出 CSV / JSON

如果你需要用 MATLAB、Python、Excel 或其他分析程序处理数据，使用 To Workspace。

步骤：

1. 添加 To Workspace；
2. 接一个正式 output；
3. 完成一次运行；
4. 选中 To Workspace；
5. 选择 CSV 或 JSON 导出。

V15 会直接从 Run Bundle 的完整 telemetry 中读取数据，不依赖当前页面只预览了多少行。

---

# 5. 常见问题

## Q：Scope 没有曲线

先检查：

1. 是否已经完成一次 Run；
2. Scope 是否真的接在 output port；
3. 该 port 是否在 Run Bundle 中有 recorder/telemetry；
4. `sourceNode` 与 `sourcePort` 是否属于同一模块。

## Q：连接了 Scope 后模型结果变了吗？

正常情况下不会。Scope/Display/To Workspace 是 observer-only。

## Q：Scope 能实时刷新吗？

V15 当前是**运行后 Scope**，不是仿真步内实时 streaming scope。
