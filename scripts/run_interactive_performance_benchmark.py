#!/usr/bin/env python3
"""
用途：测量交互式软实时 quantum、倍速、漂移、指令、遥测、并发、背压和长稳边界。
参数：默认 --profile ci 仅作短时回归；--profile formal 实际执行 30 分钟墙钟和 4 小时仿真门。
输出：写入机器可读 JSON；CI 合格但正式时长未满足时明确保留 BLOCKED，不作外推 PASS。
"""
from __future__ import annotations

import argparse
import json
import hashlib
import os
import sys
from functools import partial
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from sat_sim.bsk_engine.unified_native import WHOLE_UNIFIED_CAPABILITY_ID, UnifiedRuntimeConfig  # noqa: E402
from sat_sim.interactive.performance import load_budget, resume_formal_capacity, run_performance_benchmark  # noqa: E402
from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime  # noqa: E402


def _runtime_factory(duration_s: float, quantum_s: float, *, environment_profile='simplified', epoch_utc='2026-01-01T00:00:00Z'):
    values = {'environment_profile':environment_profile, 'epoch_utc':epoch_utc}
    if environment_profile == 'spice_wmm':
        from sat_sim.interactive.models import InteractiveSessionSpec
        from sat_sim.interactive.supervised_native_runtime import SupervisedContinuousNativeRuntime
        return SupervisedContinuousNativeRuntime(InteractiveSessionSpec(
            session_id='performance-native-owner', capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
            continuous=True, paced=False, quantum_s=quantum_s,
            task_spec={'simulation':{'duration_s':duration_s, 'sample_s':quantum_s,
                                    'solver':{'step_s':min(.1, quantum_s)}},
                       'parameters':{'values':values}},
        ))
    return UnifiedPersistentRuntime(UnifiedRuntimeConfig(
        capability_id=WHOLE_UNIFIED_CAPABILITY_ID,
        duration_s=duration_s,
        step_s=min(0.1, quantum_s),
        sample_s=quantum_s,
        adcs_only=False,
        values=values,
        events=(),
    ))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=("ci", "formal"), default="ci")
    parser.add_argument('--environment-profile', choices=('simplified','spice_wmm'), default='simplified')
    parser.add_argument('--epoch-utc', default='2026-01-01T00:00:00Z')
    parser.add_argument(
        "--budget",
        type=Path,
        default=PROJECT_ROOT / "configs" / "interactive" / "performance_budget.json",
    )
    parser.add_argument(
        "--resume-formal-capacity",
        type=Path,
        help="rerun only capacity from a complete formal report after the audited final-stop conversion fix",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "reports" / "interactive" / "performance_benchmark.json",
    )
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    progress_path = args.output.with_suffix('.progress.jsonl')
    def progress(event):
        row = {'schema_version':'sat-performance-progress.v1',
               'observed_at_utc':datetime.now(timezone.utc).isoformat(), **event}
        with progress_path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False)+'\n')
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({k:v for k,v in row.items() if k != 'measurement'}), flush=True)

    def source_fingerprint(phase):
        # Share the endurance physics/validator boundary, including pure
        # numerical kernels outside bsk_engine. Presentation changes are excluded.
        from run_native_endurance import runtime_source_manifest, runtime_source_fingerprint
        manifest = runtime_source_manifest()
        args.output.with_suffix(f'.sources.{phase}.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        return runtime_source_fingerprint(manifest)

    source_before = source_fingerprint('before')
    factory = partial(_runtime_factory, environment_profile=args.environment_profile, epoch_utc=args.epoch_utc)
    progress({'check':'warmup', 'state':'RUNNING', 'final_verdict':False})
    warmup = factory(1., .1)
    try:
        warmup.prepare()
        if not warmup.advance_to(.1):
            raise RuntimeError('PERFORMANCE_WARMUP_NO_NATIVE_TELEMETRY')
        native_warmup = warmup.finalize()
    except BaseException:
        warmup.abort()
        raise
    progress({'check':'warmup', 'state':'MEASURED', 'final_verdict':False})
    budget = load_budget(args.budget)
    if args.resume_formal_capacity:
        prior = json.loads(args.resume_formal_capacity.read_text(encoding="utf-8"))
        report = resume_formal_capacity(factory, budget, prior)
    else:
        report = run_performance_benchmark(
            factory,
            budget,
            formal=args.profile == "formal",
            progress=progress,
        )
    source_after = source_fingerprint('after')
    report['runtime'] = {'environment_profile':args.environment_profile, 'epoch_utc':args.epoch_utc,
                         'isolation':'supervised_native_process' if args.environment_profile == 'spice_wmm' else 'in_process',
                         'source_sha256_before':source_before, 'source_sha256_after':source_after,
                         'native_warmup':native_warmup}
    if source_before != source_after:
        report['status'] = 'BLOCKED'
        report['formal_passed'] = False
        report['qualification_checks_passed'] = False
        report['remaining_blockers'].append('runtime_source_changed')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if args.profile == "formal":
        return 0 if report["formal_passed"] else 1
    return 0 if report["qualification_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
