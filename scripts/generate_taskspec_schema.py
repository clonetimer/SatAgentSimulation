#!/usr/bin/env python3
"""
用途：从 Pydantic 模型重新生成公开 TaskSpec JSON Schema。
参数：--output。
输出：写入 TaskSpec Schema 文件。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sat_sim.task_models import canonical_task_spec_schema


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("src/sat_sim/schemas/task_spec.schema.json"))
    args = parser.parse_args()
    schema = {
        **canonical_task_spec_schema(),
        "$id": "https://example.local/sat-sim/task-spec-v1.schema.json",
        "title": "Satellite Simulation Canonical TaskSpec v1.0",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
