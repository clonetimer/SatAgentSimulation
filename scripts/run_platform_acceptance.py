#!/usr/bin/env python3
"""用途：在 Ubuntu/Windows 上执行当前平台、密封运行包和 vLLM 综合验收。
参数：Python、输出目录、发布身份、vLLM 地址/模型及可选跳过开关。
输出：platform_acceptance_summary.json、evidence_manifest.json 和分项证据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _run(
    command: list[str],
    *,
    stdout_path: Path,
    stderr_path: Path,
) -> int:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            stdout=stdout,
            stderr=stderr,
            text=True,
            check=False,
            env={**os.environ, "MPLCONFIGDIR": str(ROOT.parent / ".cache" / "matplotlib")},
        )
    return completed.returncode


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/platform_acceptance"))
    parser.add_argument("--expected-version", default="0.7.8")
    parser.add_argument("--expected-release-id", default="SAT-SIM-0.7.8-ENGINEERING-BASELINE")
    parser.add_argument("--vllm-base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--vllm-model", default="Qwen3.5-9B")
    parser.add_argument("--vllm-timeout-s", type=int, default=300)
    parser.add_argument("--skip-vllm", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    output = args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    # Keep the venv launcher path intact; resolving its symlink loses venv discovery.
    python = os.path.abspath(os.path.expanduser(args.python))
    checks: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str, evidence: Any = None) -> None:
        row = {"name": name, "passed": passed, "required": True, "detail": detail}
        if evidence is not None:
            row["evidence"] = evidence
        checks.append(row)

    host = {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "processor_count": os.cpu_count(),
    }
    check("supported_host", host["system"] in {"Linux", "Windows"}, host["system"], host)

    identity_path = output / "package_identity.json"
    identity_code = (
        "import importlib.metadata as m,json,sat_sim;"
        "print(json.dumps({'module_version':sat_sim.__version__,"
        "'distribution_version':m.version('satellite-simulation-platform'),"
        "'module_path':sat_sim.__file__}))"
    )
    identity_rc = _run(
        [python, "-c", identity_code],
        stdout_path=identity_path,
        stderr_path=output / "package_identity.stderr.log",
    )
    identity = _read_json(identity_path)
    identity_ok = bool(
        identity_rc == 0
        and identity
        and identity.get("module_version") == args.expected_version
        and identity.get("distribution_version") == args.expected_version
    )
    check("package_release_identity", identity_ok, f"expected={args.expected_version}", identity)

    doctor_path = output / "environment_doctor.json"
    doctor_rc = _run(
        [
            python,
            "-m",
            "sat_sim.agent_cli",
            "doctor",
            "--require-api",
            "--strict-assets",
            "--output",
            str(doctor_path),
        ],
        stdout_path=output / "doctor.stdout.log",
        stderr_path=output / "doctor.stderr.log",
    )
    doctor = _read_json(doctor_path)
    doctor_summary = (doctor or {}).get("summary", {})
    doctor_ok = bool(
        doctor_rc == 0
        and (doctor or {}).get("ok") is True
        and doctor_summary == {"PASS": 12, "WARN": 0, "FAIL": 0, "SKIP": 0}
    )
    check("strict_doctor_12_of_12", doctor_ok, f"exit={doctor_rc}", doctor_summary)

    release_dir = output / "release_check"
    release_rc = _run(
        [
            python,
            "-m",
            "sat_sim.agent_cli",
            "release-check",
            "--source-root",
            str(ROOT),
            "--strict-assets",
            "--output-dir",
            str(release_dir),
        ],
        stdout_path=output / "release_check.stdout.log",
        stderr_path=output / "release_check.stderr.log",
    )
    release_report = _read_json(release_dir / "release_closure_report.json")
    check(
        "strict_release_check",
        release_rc == 0 and (release_report or {}).get("ok") is True,
        f"exit={release_rc}",
        (release_report or {}).get("summary"),
    )

    run_root = output / "runs"
    run_id = "platform_unified_native_nominal"
    if run_root.exists():
        shutil.rmtree(run_root)
    run_rc = _run(
        [
            python,
            "-m",
            "sat_sim.agent_cli",
            "run",
            str(ROOT / "examples" / "whole_spacecraft_unified_native_nominal.yaml"),
            "--output-root",
            str(run_root),
            "--run-id",
            run_id,
        ],
        stdout_path=output / "unified_native_run.json",
        stderr_path=output / "unified_native_run.stderr.log",
    )
    run_payload = _read_json(output / "unified_native_run.json")
    check("unified_native_run", run_rc == 0 and (run_payload or {}).get("ok") is True, f"exit={run_rc}")

    inspect_path = output / "unified_native_inspect.json"
    inspect_rc = _run(
        [
            python,
            "-m",
            "sat_sim.agent_cli",
            "inspect",
            str(run_root / run_id),
        ],
        stdout_path=inspect_path,
        stderr_path=output / "unified_native_inspect.stderr.log",
    )
    inspection = _read_json(inspect_path) or {}
    run = inspection.get("run", {})
    record = run.get("run_record", {})
    validation = run.get("validation", {})
    integrity = run.get("integrity", {})
    sealed_ok = bool(
        inspect_rc == 0
        and record.get("sealed") is True
        and record.get("status") == "SUCCEEDED"
        and validation.get("result") == "PASS"
    )
    check("run_bundle_sealed", sealed_ok, f"exit={inspect_rc}", record)
    check("run_bundle_integrity", integrity.get("ok") is True, "sealed bundle hash audit", integrity)

    if args.skip_vllm:
        checks.append(
            {
                "name": "vllm_acceptance",
                "passed": False,
                "required": False,
                "detail": "skipped by --skip-vllm",
            }
        )
    else:
        vllm_dir = output / "vllm"
        vllm_rc = _run(
            [
                python,
                str(ROOT / "scripts" / "run_vllm_acceptance.py"),
                "--base-url",
                args.vllm_base_url,
                "--model",
                args.vllm_model,
                "--timeout-s",
                str(args.vllm_timeout_s),
                "--max-output-tokens",
                "8192",
                "--require-loopback",
                "--output-dir",
                str(vllm_dir),
            ],
            stdout_path=output / "vllm_acceptance.stdout.log",
            stderr_path=output / "vllm_acceptance.stderr.log",
        )
        vllm = _read_json(vllm_dir / "vllm_acceptance_report.json")
        summary = (vllm or {}).get("summary", {})
        vllm_ok = bool(
            vllm_rc == 0
            and (vllm or {}).get("status") == "PASS"
            and summary.get("taskspec_passed") == 3
            and summary.get("actual_model_invocation_verified_count") == 3
            and summary.get("generated_script_executed_count") == 3
            and summary.get("run_bundle_validation_passed_count") == 3
            and summary.get("fallback_detected") is False
        )
        check("vllm_acceptance", vllm_ok, f"exit={vllm_rc}", summary)

    required_failed = sum(1 for row in checks if row["required"] and not row["passed"])
    summary_path = output / "platform_acceptance_summary.json"
    summary_payload = {
        "schema_version": "sat-sim.platform-acceptance.v1",
        "release_id": args.expected_release_id,
        "release_version": args.expected_version,
        "target_os": host["system"],
        "status": "PASS" if required_failed == 0 else "FAIL",
        "required_failed_count": required_failed,
        "checks": checks,
        "host": host,
    }
    summary_path.write_text(
        json.dumps(summary_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    evidence_files = sorted(
        path for path in output.rglob("*") if path.is_file() and path.name != "evidence_manifest.json"
    )
    manifest = {
        "schema_version": "sat-sim.platform-evidence-manifest.v1",
        "release_version": args.expected_version,
        "files": [
            {
                "path": path.relative_to(output).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in evidence_files
        ],
    }
    manifest["file_count"] = len(manifest["files"])
    (output / "evidence_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": summary_payload["status"], "checks": checks}, ensure_ascii=False))
    return 0 if required_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
