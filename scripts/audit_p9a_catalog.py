"""用途：导出 P9 对象/参量来源与命名覆盖审计，不运行物理模型。
参数：--output-dir 输出目录。
输出：对象树 JSON、已审定命名 CSV、未审定来源 CSV、审计摘要 JSON。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from sat_sim.interactive import interactive_enabled
from sat_sim.interactive.runtime_factory import runtime_capability_catalog
from sat_sim.presentation_objects import build_scene_object_catalog
from sat_sim.quantity_dictionary import quantity_catalog, validate_dictionary
from sat_sim.workbench_catalog import workbench_presentation_catalog


def audit(output_dir: Path) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    presentation = workbench_presentation_catalog()
    scene = build_scene_object_catalog(presentation, runtime_capability_catalog(), service_enabled=interactive_enabled())
    capabilities = sorted({v["capability_id"] for o in presentation["objects"] for v in o.get("variants", [])})
    reviewed, pending, errors, coverage = [], [], [], []
    for capability in capabilities:
        try:
            payload = quantity_catalog(capability)
        except (KeyError, ValueError, TypeError) as exc:
            errors.append({"capability_id": capability, "error": str(exc)})
            continue
        reviewed.extend(payload["reviewed"])
        pending.extend(payload["unreviewed"])
        coverage.append({"capability_id": capability, **payload["coverage"]})
    validation = validate_dictionary()
    summary = {
        "schema_version": "sat.p9a-audit.v1", "object_counts": scene["counts"],
        "capabilities_inspected": len(coverage), "capabilities_expected": len(capabilities),
        "reviewed_quantities": len(reviewed), "unreviewed_quantities": len(pending),
        "dictionary_complete": not pending and not errors,
        "binding_validation": validation, "source_errors": errors, "coverage": coverage,
        "scope": "catalog and dictionary audit; not physical execution or Windows desktop acceptance",
    }
    for filename, data in [("scene_objects.json", scene), ("audit_summary.json", summary)]:
        (output_dir / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    columns = ["source_capability_id", "kind", "source_field", "display_name_zh", "unit", "object_id", "owner_scope", "instance_index", "aggregation", "review_status"]
    for filename, rows in [("reviewed_quantities.csv", reviewed), ("unreviewed_quantities.csv", pending)]:
        with (output_dir / filename).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("reports/p9a/catalog"))
    args = parser.parse_args()
    summary = audit(args.output_dir)
    print(json.dumps({k: v for k, v in summary.items() if k != "coverage"}, ensure_ascii=False, indent=2))
    return 0 if summary["binding_validation"]["ok"] and not summary["source_errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
