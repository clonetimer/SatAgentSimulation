#!/usr/bin/env python3
"""Install only the explicitly attested Basilisk 2.11.0+satfix1 binary wheel.

用途：对平台匹配的本地 satfix1 Basilisk wheel 逐文件验证 SHA-256/RECORD 后再离线安装。
参数：--wheelhouse 指定本地目录，--check-only 仅核对不修改环境，--report 输出审计 JSON。
输出：成功打印已验证的 wheel 来源与实际 bsk 版本；原生模块导入须在 NumPy 等依赖安装后进行。
"""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

from bsk_wheel_provenance import verify_approved_wheel, REPORT


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheelhouse", type=Path, default=root / "third_party" / "wheels")
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    record = verify_approved_wheel(args.wheelhouse)
    record["operation"] = "CHECK_ONLY" if args.check_only else "INSTALL"
    if not args.check_only:
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--no-index", "--no-deps",
            "--force-reinstall", record["wheel_path"],
        ], check=True)
        installed = version("bsk")
        if installed != "2.11.0+satfix1":
            raise RuntimeError("BSK_INSTALL_VERSION_MISMATCH:" + installed)
        # Native C++ import requires numpy, which is installed during the
        # NEXT bootstrap phase. Importing it here after --no-deps is unsafe
        # in a truly clean Python environment; the post-install doctor/CI
        # must run an actual Basilisk module smoke after resolving deps.
        record["native_import_deferred_until_dependencies"] = True
        record["installed_version"] = installed
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(record, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
