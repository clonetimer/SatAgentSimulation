#!/usr/bin/env python3
"""
用途：执行或预检 AstroGraph 反作用轮 18 案例 Basilisk 原生信号门，并汇总技术资格结果。
参数：--config 指定信号门配置；--output 指定输出目录；--dry-run 仅展开和严格校验；--report 写入摘要。
输出：生成 Campaign、实验记录、配对验证和资格索引；Basilisk 缺失返回 2，技术门失败返回 1。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sat_sim.campaign import expand_campaign_spec  # noqa: E402
from sat_sim.fault_dataset_factory import (  # noqa: E402
    FaultDatasetFactoryConfig,
    build_fault_dataset_campaign,
    run_fault_dataset_campaign,
)
from sat_sim.simulation_fidelity import inspect_basilisk_runtime  # noqa: E402
from sat_sim.diagnostic_signature_library import list_diagnostic_signatures  # noqa: E402
from sat_sim.task_validator import validate_task_spec  # noqa: E402


def _write(path: Path | None, payload: dict) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="configs/integration/rw_native_validation.json")
    parser.add_argument("--output", default="datasets/rw_native_validation")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    config = FaultDatasetFactoryConfig.from_json(args.config)
    spec = build_fault_dataset_campaign(config)
    plan = expand_campaign_spec(spec, output_root=args.output)
    validation_failures: dict[str, list[str]] = {}
    for case in plan.cases:
        result = validate_task_spec(case.spec)
        if not result.ok:
            validation_failures[case.case_id] = [f"{issue.path}: {issue.message}" for issue in result.errors]
    base_report = {
        "schema_version": "sat-sim.astrograph-basilisk-signal-gate.v1",
        "case_count": len(plan.cases),
        "pair_count": len(plan.cases) // 2,
        "expected_case_count": 18,
        "strict_taskspec_validation_failures": validation_failures,
        "basilisk_runtime": inspect_basilisk_runtime().to_dict(),
        "dry_run": bool(args.dry_run),
        "diagnostic_signature_ids": [item.signature_id for item in list_diagnostic_signatures()],
        "diagnostic_threshold_policy": "provisional until 18-case Basilisk evidence and named expert freeze",
    }
    if len(plan.cases) != 18 or validation_failures:
        base_report.update({"status": "FAIL_PLAN", "formal_training_qualified_pair_count": 0})
        _write(args.report, base_report)
        print(json.dumps(base_report, ensure_ascii=False, indent=2))
        return 1
    if args.dry_run:
        result = run_fault_dataset_campaign(spec, output_root=args.output, dry_run=True, continue_on_error=False)
        base_report.update({
            "status": "PASS_DRY_RUN",
            "campaign_status": result.campaign.manifest.get("status"),
            "formal_training_qualified_pair_count": 0,
            "claim_boundary": "plan validation only; no Basilisk runtime or physics result was signed",
        })
        _write(args.report, base_report)
        print(json.dumps(base_report, ensure_ascii=False, indent=2))
        return 0

    if not base_report["basilisk_runtime"]["ready"]:
        base_report.update({
            "status": "BLOCKED_RUNTIME",
            "formal_training_qualified_pair_count": 0,
            "claim_boundary": "Basilisk unavailable; proxy execution is prohibited for this gate",
        })
        _write(args.report, base_report)
        print(json.dumps(base_report, ensure_ascii=False, indent=2))
        return 2

    result = run_fault_dataset_campaign(spec, output_root=args.output, dry_run=False, continue_on_error=False)
    qualification = result.dataset_index.get("qualification", {})
    diagnostic = result.dataset_index.get("diagnostic_qualification", {})
    passed_pairs = int(qualification.get("formal_training_qualified_pair_count", 0))
    diagnostic_candidates = int(diagnostic.get("basilisk_candidate_qualified_pair_count", 0))
    counts = qualification.get("counts_by_decision", {})
    diagnostic_counts = diagnostic.get("counts_by_decision", {})
    fault_summaries = diagnostic.get("fault_summaries", [])
    signatures_ready = bool(fault_summaries) and all(
        item.get("threshold_freeze_status") == "READY_FOR_EXPERT_REVIEW" for item in fault_summaries
    )
    success = (
        result.campaign.summary.get("failure_count") == 0
        and qualification.get("pair_count") == 9
        and passed_pairs == 9
        and counts == {"PASS": 9}
        and diagnostic.get("pair_count") == 9
        and diagnostic_candidates == 9
        and diagnostic_counts == {"PASS": 9}
        and signatures_ready
    )
    base_report.update({
        "status": "PASS_DIAGNOSTIC_CANDIDATE_GATE" if success else "FAIL_DIAGNOSTIC_CANDIDATE_GATE",
        "campaign_status": result.campaign.manifest.get("status"),
        "qualification": qualification,
        "diagnostic_qualification": diagnostic,
        "formal_training_qualified_pair_count": passed_pairs,
        "basilisk_diagnostic_candidate_pair_count": diagnostic_candidates,
        "formal_diagnostic_qualified_pair_count": diagnostic.get("formal_diagnostic_qualified_pair_count", 0),
        "training_ready_case_count": result.dataset_index.get("training_ready_case_count", 0),
        "signature_expert_freeze_required": True,
        "dataset_pair_expert_review_required": True,
    })
    _write(args.report, base_report)
    print(json.dumps(base_report, ensure_ascii=False, indent=2))
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
