#!/usr/bin/env python3
"""
用途：确保正式仿真使用已批准的 Basilisk 运行时，必要时获取官方 Wheel、校验并安装 metadata-only satfix2。
参数：--check-only 仅检查；--report 写入机器可读状态 JSON。
输出：打印安装/验证状态；成功返回 0，运行时仍不可用返回 2；不修改 Basilisk 仿真二进制载荷。
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# During first-time installation, PyYAML/numpy/pydantic etc. do not yet
# exist. Importing sat_sim.simulation_fidelity through sat_sim.__init__
# transitively loads these application dependencies, breaking clean Windows
# and Linux bootstrap before pip can install them.
import importlib.util
_fidelity_path = ROOT / "src" / "sat_sim" / "simulation_fidelity.py"
_spec = importlib.util.spec_from_file_location(
    "_sat_bootstrap_fidelity_lightweight", _fidelity_path,
)
if _spec is None or _spec.loader is None:
    raise RuntimeError("BASILISK_BOOTSTRAP_FIDELITY_MODULE_MISSING")
_fidelity = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _fidelity
_spec.loader.exec_module(_fidelity)
inspect_basilisk_runtime = _fidelity.inspect_basilisk_runtime

from bsk_wheel_provenance import verify_approved_wheel


def _platform_key() -> str:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system.startswith("win") and machine in {"amd64", "x86_64"}:
        return "windows_x86_64"
    if system == "linux" and machine in {"amd64", "x86_64"}:
        return "linux_x86_64"
    raise RuntimeError(f"no approved prebuilt Basilisk wheel policy for platform={system}/{machine}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true", help="Only inspect; do not download or install.")
    parser.add_argument("--report", type=Path, help="Write machine-readable status JSON.")
    parser.add_argument("--wheel", type=Path, help="Install a local approved bsk wheel with --no-deps before other methods.")
    parser.add_argument("--skip-pip-no-deps", action="store_true", help="Skip the first python -m pip install bsk --no-deps attempt.")
    parser.add_argument(
        "--require-satfix2", action="store_true",
        help="For release/Windows installation: require an attested metadata-only 2.11.0+satfix2 wheel.",
    )
    args = parser.parse_args()
    if args.require_satfix2 and args.wheel:
        parser.error("--require-satfix2 does not permit arbitrary --wheel overrides")

    before = inspect_basilisk_runtime()
    report: dict[str, object] = {
        "schema_version": "sat-sim.basilisk-runtime-bootstrap.v1",
        "python": sys.version.split()[0],
        "platform": {"system": platform.system(), "machine": platform.machine()},
        "before": before.to_dict(),
        "action": "none",
        "status": "PASS" if before.ready else "BLOCKED",
    }
    try:
        if args.require_satfix2:
            report["distribution_policy"] = "AUDITED_METADATA_ONLY_SATFIX2"
            if args.check_only:
                report["status"] = (
                    "PASS" if before.ready and before.version == "2.11.0+satfix2"
                    else "BLOCKED"
                )
                report["reason"] = (
                    "installed_version_checked_only_not_installer_provenance"
                    if report["status"] == "PASS" else "SATFIX2_REQUIRED"
                )
            else:
                wheelhouse = ROOT / "third_party" / "wheels"
                try:
                    verified = verify_approved_wheel(wheelhouse)
                except (FileNotFoundError, ValueError, OSError, RuntimeError):
                    # Always acquire the exact official precompiled binary,
                    # check the pinned upstream SHA-256, then rewrite ONLY
                    # the Pillow dependency metadata; never use the R9-K
                    # experimental C++ SimpleBattery wheel as production bsk.
                    key = _platform_key()
                    subprocess.run([
                        sys.executable,
                        str(ROOT / "scripts" / "fetch_official_bsk_and_build_offline_bundle.py"),
                        "--platform", key,
                    ], cwd=ROOT, check=True)
                    verified = verify_approved_wheel(wheelhouse)
                report["approved_wheel"] = {
                    "filename": verified["filename"],
                    "sha256": verified["sha256"],
                    "official_source": verified["official_source"],
                    "official_sha256": verified["official_sha256"],
                    "record_verified": verified["record_verified"],
                    "metadata_only_change": verified["metadata_only_change"],
                }
                subprocess.run([
                    sys.executable, str(ROOT / "scripts" / "install_offline_bsk.py"),
                    "--wheelhouse", str(wheelhouse),
                ], cwd=ROOT, check=True)
                report["action"] = "install_verified_satfix2_metadata_only"
        elif not before.ready and not args.check_only:
            attempts: list[dict[str, object]] = []
            report["attempts"] = attempts
            if args.wheel:
                command = [sys.executable, "-m", "pip", "install", str(args.wheel), "--no-deps"]
                completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                attempts.append({"method": "local_wheel_no_deps", "command": command, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]})
            if not inspect_basilisk_runtime().ready and not args.skip_pip_no_deps:
                command = [sys.executable, "-m", "pip", "install", "bsk==2.11.0", "--no-deps"]
                completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
                attempts.append({"method": "pip_bsk_no_deps", "command": command, "returncode": completed.returncode, "stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]})
            if not inspect_basilisk_runtime().ready:
                key = _platform_key()
                report["action"] = "pip_no_deps_then_fetch_verify_metadata_patch_install"
                try:
                    subprocess.run(
                        [sys.executable, str(ROOT / "scripts" / "fetch_official_bsk_and_build_offline_bundle.py"), "--platform", key],
                        cwd=ROOT, check=True,
                    )
                    subprocess.run([sys.executable, str(ROOT / "scripts" / "install_offline_bsk.py")], cwd=ROOT, check=True)
                    attempts.append({"method": "offline_verified_bundle", "returncode": 0})
                except subprocess.CalledProcessError as exc:
                    attempts.append({"method": "offline_verified_bundle", "returncode": exc.returncode, "error": str(exc)})
        after = inspect_basilisk_runtime()
        report["after"] = after.to_dict()
        report["status"] = (
            "PASS" if after.ready and (
                not args.require_satfix2 or after.version == "2.11.0+satfix2"
            ) else "BLOCKED"
        )
        if report["status"] != "PASS":
            report["reason"] = (
                "SATFIX2_REQUIRED" if after.ready and args.require_satfix2
                else after.reason
            )
    except Exception as exc:
        report["status"] = "BLOCKED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["after"] = inspect_basilisk_runtime().to_dict()
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
