#!/usr/bin/env python3
"""
用途：运行批准经验与无经验基线的真实 vLLM 配对纵向 A/B。
参数：指定密封来源 Run Bundle、模型服务、模型名和报告目录。
输出：保存逐对模型/运行证据、Token/延迟差异、符号检验和门禁报告。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.experience_longitudinal_eval import run_experience_longitudinal_eval


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="Qwen3.5-9B")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experience_longitudinal_eval"),
    )
    args = parser.parse_args()
    report = run_experience_longitudinal_eval(
        source_bundle=args.source_bundle,
        output_dir=args.output_dir,
        base_url=args.base_url,
        model=args.model,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
