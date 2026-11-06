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

from sat_sim.experience_longitudinal_eval import (
    DEFAULT_HELD_OUT_REQUESTS,
    run_experience_longitudinal_eval,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-bundle", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", default="Qwen3.5-9B")
    parser.add_argument("--max-repair-attempts", type=int, default=2, choices=(0, 1, 2))
    parser.add_argument(
        "--requests-file",
        type=Path,
        help=(
            "Optional frozen A/B request set as a JSON array of six or more "
            "non-empty strings. Source strings are preserved verbatim."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/experience_longitudinal_eval"),
    )
    args = parser.parse_args()
    requests = DEFAULT_HELD_OUT_REQUESTS
    if args.requests_file is not None:
        try:
            raw_requests = json.loads(args.requests_file.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            parser.error(f"cannot read frozen requests JSON: {exc}")
        if (
            not isinstance(raw_requests, list)
            or len(raw_requests) < 6
            or any(not isinstance(item, str) or not item.strip() for item in raw_requests)
        ):
            parser.error("--requests-file must contain a JSON array of >=6 nonempty strings")
        requests = tuple(raw_requests)

    report = run_experience_longitudinal_eval(
        source_bundle=args.source_bundle,
        output_dir=args.output_dir,
        base_url=args.base_url,
        model=args.model,
        max_repair_attempts=args.max_repair_attempts,
        requests=requests,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
