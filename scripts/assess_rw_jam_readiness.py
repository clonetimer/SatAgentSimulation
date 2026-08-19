#!/usr/bin/env python3
"""
用途：审查 Phase 3-H RW_JAM 的 Basilisk 运行时、遥测接口、执行器参数合同和18案例原生门准备度。
参数：--output 指定 JSON 报告；--executed-native-cases 与 --a-level-cases 记录已完成的原生和A级案例数。
输出：写出 fail-closed 的 RW_JAM 转正准备度报告；未满足正式条件时返回退出码2，且不启用代理回退。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.rw_jam_readiness import (
    NativeCampaignReadiness, Phase3HReadinessGate, Phase3HReadinessRequest,
    actuator_readiness_from_contract, inspect_basilisk_runtime,
)
from sat_sim.rw_actuator_contract import (
    load_reference_rw_actuator_contract,
    load_rw_actuator_contract,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="phase3h_rw_jam_readiness.json")
    parser.add_argument(
        "--actuator-contract",
        help="Optional actuator-contract JSON; defaults to the package-resident engineering baseline.",
    )
    parser.add_argument(
        "--native-gate-report",
        help="Derive executed native-case count from a PASS diagnostic-candidate gate report.",
    )
    parser.add_argument(
        "--dataset-root",
        help="Derive A-level approval count from the reviewed AstroGraph dataset index.",
    )
    parser.add_argument("--executed-native-cases", type=int, default=0)
    parser.add_argument("--a-level-cases", type=int, default=0)
    args = parser.parse_args()
    contract = (
        load_rw_actuator_contract(args.actuator_contract)
        if args.actuator_contract
        else load_reference_rw_actuator_contract()
    )
    executed_native_cases = args.executed_native_cases
    if args.native_gate_report:
        gate_report = json.loads(Path(args.native_gate_report).read_text(encoding="utf-8"))
        gate_passed = all((
            gate_report.get("status") == "PASS_DIAGNOSTIC_CANDIDATE_GATE",
            gate_report.get("case_count") == 18,
            gate_report.get("pair_count") == 9,
            gate_report.get("formal_training_qualified_pair_count") == 9,
            gate_report.get("basilisk_diagnostic_candidate_pair_count") == 9,
        ))
        if not gate_passed:
            raise ValueError("native gate report does not prove an 18-case/9-pair candidate PASS")
        executed_native_cases = 18

    a_level_cases = args.a_level_cases
    if args.dataset_root:
        dataset_root = Path(args.dataset_root)
        dataset_index = json.loads(
            (dataset_root / "astrograph_dataset_index.json").read_text(encoding="utf-8")
        )
        fault_summaries = (
            (dataset_index.get("diagnostic_qualification") or {}).get("fault_summaries")
            or []
        )
        reviews_ready = all((
            dataset_index.get("training_ready_case_count") == 18,
            dataset_index.get("expert_approved_case_count") == 18,
            len(fault_summaries) == 3,
            all(item.get("threshold_freeze_status") == "FROZEN" for item in fault_summaries),
            len(list((dataset_root / "validation" / "signature_reviews").glob("*.json"))) == 3,
            len(list((dataset_root / "validation" / "expert_reviews").glob("*.json"))) == 9,
        ))
        if not reviews_ready:
            raise ValueError("dataset root does not prove 18 reviewed A-level cases")
        a_level_cases = 18

    result = Phase3HReadinessGate().assess(Phase3HReadinessRequest(
        basilisk_runtime=inspect_basilisk_runtime(),
        actuator_contract=actuator_readiness_from_contract(contract),
        campaign=NativeCampaignReadiness(
            executed_native_cases=executed_native_cases, a_level_cases=a_level_cases,
        ),
    ))
    Path(args.output).write_text(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status": result.status.value, "blockers": result.blockers}, ensure_ascii=False))
    return 0 if result.formal_training_ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
