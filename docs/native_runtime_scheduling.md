# 原生连续仿真的 CPU 调度策略

监督式连续原生子进程在构建物理图前读取 `SAT_SIM_NATIVE_CPU_POLICY`，默认值为 `prefer_performance`。Windows 支持且允许的 CPU 集合内存在不同性能等级时，选择最高等级处理器，仅作用于该子进程。

实现保留初始进程亲和性边界，排除停驻、分配给其他进程及实时保留的 CPU。不支持的多处理器组、同质 CPU 或无候选时保留系统调度并记录原因。读取或应用失败时恢复原亲和性；恢复失败则终止启动。无效策略值拒绝启动。

在启动应用前，可在 PowerShell 中明确保留系统调度：

```powershell
$env:SAT_SIM_NATIVE_CPU_POLICY = 'system'
```

恢复默认偏好：

```powershell
$env:SAT_SIM_NATIVE_CPU_POLICY = 'prefer_performance'
```

此策略不改变全局电源计划、进程优先级、计时器分辨率、物理步长或计算精度。非 Windows 平台保留系统调度。等级语义依据 [Microsoft SYSTEM_CPU_SET_INFORMATION 文档](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-system_cpu_set_information)：较高 EfficiencyClass 表示较高性能、较低能效。

会话状态的 `native_cpu_scheduling` 和封存结果的 `metadata.native_cpu_scheduling` 使用 `sat-native-cpu-scheduling.v1` 格式，记录策略、实际模式、应用状态、子进程 PID、允许与选中的亲和性（可用时）及原因。无法应用时明确披露。

## 验证与边界

本机 B-A-B-A 对照中，系统调度为 7.797619、7.747365 倍，性能核心为 11.068150、11.247870 倍；四次完整遥测逐字节相同。证据：`reports/local_requirements_completion/native-scheduling-crossover/report.json`。这是局部诊断，不保证所有硬件和负载下的性能。

生产短测的 10 倍速目标实际达到 9.974895 倍，但未覆盖正式漂移和长仿真窗口，正式状态仍为 BLOCKED。其源码为 `2f7cf17bd13678994e6fd08834a7da4a1d9a18f57813636d1eccd7acc8b71fbb`；随后完善错误处理和元数据校验，不能继承其资格。

最终专项为 15 通过、3 跳过。源码 `055fc17886f126821fdf1a95fdd05f3f60d8882f744754f34d51e779610b66c1` 正在重新执行完整 Windows 回归、实际原生浏览器、异常留证与场景重建、正式性能。队列见 `reports/local_requirements_completion/post-training-validation-qa-native-scheduling-v4/state.json`。真实 24 小时耐久仍需独立完成。
