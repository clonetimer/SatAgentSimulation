"""
用途：冻结软实时扩展实施前后的测试、Doctor、OpenAPI 和脚本资产兼容基线。
参数：--output 指定机器可读 JSON 报告；默认写入 reports/interactive/baseline_compatibility.json。
输出：生成可重放的哈希与计数报告；所有冻结证据有效时返回 0，否则返回 1。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

from sat_sim.api import create_app
from sat_sim.interactive import FEATURE_FLAG_ENV, interactive_enabled

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _doctor(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": _sha256(path), "pass": summary.get("PASS"), "fail": summary.get("FAIL"), "warn": summary.get("WARN")}


def build_report() -> dict:
    collected = subprocess.run(
        [str(ROOT.parent / ".venv" / "bin" / "python"), "-m", "pytest", "--collect-only", "-q"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    match = re.search(r"(\d+) tests collected", collected.stdout)
    with tempfile.TemporaryDirectory(prefix="sat-interactive-baseline-") as tmp:
        root = Path(tmp)
        app = create_app(runs_root=root / "runs", artifacts_root=root / "artifacts", embedded_worker=False)
        openapi = json.dumps(app.openapi(), sort_keys=True, separators=(",", ":")).encode("utf-8")
    doctor = _doctor(ROOT / "reports" / "environment_doctor.json")
    release = _doctor(ROOT / "reports" / "release_check" / "environment_doctor.json")
    passed = bool(
        collected.returncode == 0
        and match
        and int(match.group(1)) >= 641
        and doctor == {**doctor, "pass": 12, "fail": 0, "warn": 0}
        and release == {**release, "pass": 12, "fail": 0, "warn": 0}
        and not interactive_enabled()
    )
    return {
        "schema_version": "interactive-baseline.v1",
        "status": "PASS" if passed else "FAIL",
        "claim_boundary": "Internal engineering simulation; not hardware or flight validation.",
        "frozen_pre_extension_pytest_count": 641,
        "current_collected_pytest_count": int(match.group(1)) if match else None,
        "pytest_collect_exit_code": collected.returncode,
        "strict_doctor": doctor,
        "strict_release_check": release,
        "openapi": {"path_count": len(app.openapi()["paths"]), "canonical_sha256": hashlib.sha256(openapi).hexdigest()},
        "script_inventory": {"path": "scripts/SCRIPT_INVENTORY.md", "sha256": _sha256(ROOT / "scripts" / "SCRIPT_INVENTORY.md")},
        "feature_flag": {"environment": FEATURE_FLAG_ENV, "enabled_during_audit": interactive_enabled(), "required_default": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "reports" / "interactive" / "baseline_compatibility.json")
    args = parser.parse_args()
    report = build_report()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
