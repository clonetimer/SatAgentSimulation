# 03｜ADCS + Reaction Wheel：第一次真正的模块装配

## 本课目标

完成一次最典型的类 Simulink 操作：

```text
加载 ADCS 父模型
  → 插入兼容 Reaction Wheel
  → 校验接口
  → 运行
  → 与 baseline 比较
```

---

# 1. 加载快速模板

进入：

```text
图形组装 → 多模块装配 · V15
```

选择：

```text
ADCS + Reaction Wheel · 可运行组合
```

这个模板的父 Capability 是：

```text
subsystem.adcs_fidelity.v1
```

它包含一个可替换的 `reaction_wheel` internal slot。

---

# 2. 看懂图，不需要先懂控制理论

你会看到类似：

```text
Wheel Allocator ─────→ Rigid Body
       │
       ▼
Reaction Wheel ──────→ Saturation Monitor
```

简单理解：

- Wheel Allocator：决定要给轮子多少控制力矩；
- Reaction Wheel：根据电机力矩推进轮速状态；
- Rigid Body：处理刚体姿态动力学；
- Saturation Monitor：检查轮速等状态是否超界。

---

# 3. 为什么 Reaction Wheel 能插进去

不是因为名字叫 Reaction Wheel。

平台会检查正式接口，例如：

```text
motor_torque_nm
   ↓
reaction wheel dynamics
   ↓
wheel_speed_rad_s
```

并检查：

- schema；
- dtype / shape；
- unit；
- timing；
- fan-in/fan-out；
- allow-list。

所以“拖得进去”意味着后端合同也认为它兼容。

---

# 4. 尝试恢复 baseline

在 Reaction Wheel 槽位中恢复：

```text
父适配器内部实现 / baseline
```

此时模型仍能运行，只是轮速推进使用父 Adapter 的内部实现。

然后重新选择：

```text
component.reaction_wheel.v1
```

你就完成了一次真实模块替换。

---

# 5. 校验和运行

依次执行：

```text
校验装配
生成 Python
构建并运行
```

运行摘要中应能看到类似：

```text
reaction_wheel_provider_capability_id
= component.reaction_wheel.v1
```

这比“节点颜色变了”更重要，因为它证明运行时真的切换了 provider。

---

# 6. 一键与 baseline 对比

点击：

```text
与基线对比
```

系统会自动：

1. 运行当前 replacement；
2. 恢复 baseline selection；
3. 运行 baseline；
4. 对共同 QoI 做差值。

第一次使用时重点看：

```text
qoi.adcs.final_pointing_error_deg
qoi.adcs.initial_pointing_error_deg
qoi.adcs.environment.total_torque_norm_nm
```

不要要求 replacement 一定“更好”；这里的目的首先是确认两种实现的物理结果是否一致、合理或存在预期差异。

---

# 7. 本课完成标准

- [ ] 能加载 ADCS + RW 快速模板；
- [ ] 能识别 Reaction Wheel slot；
- [ ] 能在 baseline 和 `component.reaction_wheel.v1` 间切换；
- [ ] replacement 运行后 provider ID 正确；
- [ ] 能完成一次 baseline comparison。
