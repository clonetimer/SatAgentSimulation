#!/usr/bin/env python3
"""
用途：确保正式仿真使用已批准的 Basilisk 运行时，必要时获取官方 Wheel、校验并安装 metadata-only satfix1。
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
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sat_sim.simulation_fidelity import inspect_basilisk_runtime  # noqa: E402


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
    args = parser.parse_args()

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
        if not before.ready and not args.check_only:
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
        report["status"] = "PASS" if after.ready else "BLOCKED"
        if not after.ready:
            report["reason"] = after.reason
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
