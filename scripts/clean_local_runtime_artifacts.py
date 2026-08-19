"""用途：在 Ubuntu/Linux 上清理项目内的本地运行副产物。
参数：--project-root、--apply、--report 和可重复的 --extra。
输出：删除记录或 dry-run 记录，并生成 cleanup_report.json。
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path


DEFAULT_TOP_LEVEL_TARGETS = (
    ".pytest_cache",
    ".sat_sim_api",
    "generated_tasks",
    "runs",
)


def _inside_root(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return path != root


def _collect_targets(root: Path, extras: list[str]) -> tuple[list[Path], int]:
    candidates: set[Path] = set()

    for name in DEFAULT_TOP_LEVEL_TARGETS:
        path = root / name
        if path.exists():
            candidates.add(path.resolve())

    for value in extras:
        relative = Path(value)
        if relative.is_absolute():
            raise ValueError(f"--extra must be project-relative: {value}")
        path = (root / relative).resolve()
        if not _inside_root(path, root):
            raise ValueError(f"refuse to clean outside project root: {value}")
        if path.exists():
            candidates.add(path)

    candidates.update(path.resolve() for path in root.rglob("__pycache__"))
    candidates.update(
        path.resolve()
        for pattern in ("*.pyc", "*.pyo")
        for path in root.rglob(pattern)
    )

    ordered = sorted(candidates, key=lambda path: (len(path.parts), str(path)))
    selected: list[Path] = []
    for path in ordered:
        if not _inside_root(path, root):
            raise ValueError(f"refuse to clean outside project root: {path}")
        if any(parent == path or parent in path.parents for parent in selected):
            continue
        selected.append(path)
    return selected, len(candidates) - len(selected)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Delete selected artifacts. Without this flag, only report a dry run.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=Path("reports/cleanup_report.json"),
    )
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="Additional project-relative target; may be repeated.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    root = args.project_root.resolve(strict=True)
    report_path = args.report if args.report.is_absolute() else root / args.report
    report_path = report_path.resolve()
    if not _inside_root(report_path, root):
        raise ValueError(f"report must stay inside project root: {report_path}")

    targets, skipped_nested = _collect_targets(root, args.extra)
    records: list[dict[str, str]] = []
    for target in targets:
        kind = "directory" if target.is_dir() else "file"
        action = "removed" if args.apply else "would_remove"
        if args.apply:
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        print(f"[{action.upper()}] {target}")
        records.append({"path": str(target), "kind": kind, "action": action})

    payload = {
        "schema_version": "sat-sim.cleanup-report.v1",
        "project_root": str(root),
        "mode": "executed" if args.apply else "dry_run",
        "runtime_artifact_count": len(targets),
        "skipped_nested_target_count": skipped_nested,
        "targets": records,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"runtime_artifact_count={len(targets)}")
    print(f"skipped_nested_target_count={skipped_nested}")
    print(f"cleanup_report={report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
