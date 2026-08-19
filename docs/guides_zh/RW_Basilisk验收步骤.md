# 反作用轮 18 案例 Basilisk 验收步骤

1. 创建干净的 Python 3.10～3.13 环境。
2. 执行平台引导脚本；引导脚本必须完成 Basilisk 运行时身份验证。
3. 执行：

```bash
python scripts/ensure_basilisk_runtime.py --check-only
python scripts/generate_astrograph_rw_fault_dataset.py \
  --config configs/integration/rw_native_validation.json \
  --output datasets/astrograph_rw_basilisk_signal_gate_v1
```

4. 验收 18 个案例：三个故障、三个独立种子、故障/名义配对。
5. 检查每个 `astrograph/dataset_contract.json` 与 `simulation_report.json`：
   - `fidelity_level` 必须为 `A_ENGINEERING_BASILISK_NATIVE`；
   - `formal_training_eligible` 和 `training_ready` 必须为真；
   - `bsk_version` 必须属于受支持版本；
   - 运行时模块实例化数量必须大于零。
6. 对配对数据检查故障前一致性和故障后方向：
   - 摩擦增加：等效摩擦力矩上升，并影响轮速/控制响应；
   - 卡死：目标轮有效力矩能力和轮速响应符合锁止机理；
   - 力矩权限下降：有效最大力矩按 `torque_scale` 降低，不能被标成电机完全失效。
7. 任一案例失败时，不执行 90 案例扩展，也不向 AstroGraph 发布正式数据。
