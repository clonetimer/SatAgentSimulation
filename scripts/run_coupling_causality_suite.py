#!/usr/bin/env python3
"""Run sealed baseline/one-factor Basilisk coupling validation cases.

用途：执行共享基线与单因素扰动的真实 Basilisk 配对试验，验证跨分系统耦合的源变化、响应方向、持续性和时延。
参数：--matrix 指定案例矩阵；--output-dir 指定全新或空证据目录；--environment-evidence 可绑定严格 Environment Doctor JSON。
输出：生成密封基线/扰动 Run Bundle、逐案例因果报告、套件汇总及 SHA-256 证据清单。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from sat_sim.coupling_validation_suite import run_coupling_validation_suite


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--environment-evidence")
    args = parser.parse_args()
    result = run_coupling_validation_suite(
        args.matrix,
        output_root=args.output_dir,
        environment_evidence=args.environment_evidence,
    )
    print(json.dumps(result.__dict__, ensure_ascii=False, indent=2))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    exit_code = main()
    # The suite runs every Basilisk case in an isolated subprocess and all
    # evidence is already sealed before main() returns.  Bypass occasional
    # SWIG/native finalizer stalls so CI observes the real suite result rather
    # than a post-report interpreter-shutdown timeout.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(exit_code))
