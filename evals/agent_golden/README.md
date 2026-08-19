# Agent Golden Set

`golden_set.json` contains 140 versioned cases:

- 25 component scenarios;
- 30 subsystem scenarios;
- 30 whole-spacecraft scenarios;
- 15 coupled/multi-output scenarios;
- 20 invalid or boundary requests;
- 10 ambiguous/conflicting/missing-information requests;
- 10 adversarial claim/proxy requests.

Regenerate deterministically:

```bash
PYTHONPATH=src:. python scripts/generate_v27_golden_set.py
```

Run the formal evaluator:

```bash
sat-agent eval \
  --manifest evals/agent_golden/golden_set.json \
  --repeat-count 5 \
  --output-dir reports/agent_golden_v27
```

The evaluator records false accepts/rejects, level misrouting, effect-owner
errors, required-QoI availability, over-claiming, silent degradation, TaskSpec
and plan reproducibility, route/claim flips, `pass@1`, `pass^3`, and `pass^5`.
