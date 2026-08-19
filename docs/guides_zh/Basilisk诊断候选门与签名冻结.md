# Basilisk 诊断候选门与签名冻结

## 1. 18 案例门

正式入口：

```bash
python scripts/run_astrograph_basilisk_signal_gate.py \
  --config configs/integration/rw_native_validation.json \
  --output datasets/astrograph_rw_basilisk_signal_gate_v1
```

该门要求：

1. 18 个子 TaskSpec 全部严格校验；
2. 9 个故障/名义 Pair 全部通过 Basilisk 物理门；
3. 9 个 Pair 全部通过诊断签名候选门；
4. 每类故障至少有 3 个独立 A 级候选 Pair；
5. 代理回退被禁止。

成功状态为 `PASS_DIAGNOSTIC_CANDIDATE_GATE`，这仍不是训练批准。

## 2. 签名冻结

每类故障达到候选门后，具名专家执行：

```bash
python scripts/review_diagnostic_signature.py DATASET_ROOT FAULT_ID \
  --decision FREEZE \
  --reviewer-id expert-001 \
  --reviewer-type llm_assisted_human \
  --notes "已复核三组独立 Basilisk Pair 与阈值。"
```

冻结绑定精确 `signature_sha256`。修改 YAML 阈值后哈希变化，必须重新运行候选门并重新审核。

## 3. 双专家门

签名冻结解决“阈值方法是否可接受”；`review_astrograph_dataset_pair.py` 解决“具体 Pair 是否可进入受控训练”。两者不可互相替代。
