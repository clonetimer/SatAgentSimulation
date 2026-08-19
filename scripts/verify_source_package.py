#!/usr/bin/env python3
"""用途：校验当前洁净源码树的发布身份、必需资产和禁止副产物。
参数：--output 可选写入机器可读报告。
输出：source-package-self-check JSON；缺失或残留副产物时返回非零。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sat_sim.release_closure import (  # noqa: E402
    RELEASE_ID as EXPECTED_RELEASE_ID,
    RELEASE_VERSION as EXPECTED_RELEASE_VERSION,
)


FORBIDDEN_TOP_LEVEL = {".pytest_cache", ".sat_sim_api", "generated_tasks", "runs"}
FORBIDDEN_DIR_NAMES = {"__pycache__", ".mypy_cache", ".ruff_cache"}
FORBIDDEN_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    manifest_path = ROOT / "src" / "sat_sim" / "release_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = list(
        dict.fromkeys(
            [
                "README.md",
                "pyproject.toml",
                "src/sat_sim/release_manifest.json",
                *manifest.get("required_documents", []),
                *manifest.get("required_files", []),
            ]
        )
    )
    missing = [relative for relative in required if not (ROOT / relative).is_file()]

    forbidden: list[str] = []
    for name in sorted(FORBIDDEN_TOP_LEVEL):
        if (ROOT / name).exists():
            forbidden.append(name + "/")
    for path in ROOT.rglob("*"):
        relative = path.relative_to(ROOT).as_posix()
        if path.is_dir() and (
            path.name in FORBIDDEN_DIR_NAMES or path.name.endswith(".egg-info")
        ):
            forbidden.append(relative + "/")
        elif path.is_file() and path.suffix.lower() in FORBIDDEN_SUFFIXES:
            forbidden.append(relative)

    identity_ok = (
        manifest.get("release_id") == EXPECTED_RELEASE_ID
        and manifest.get("release_version") == EXPECTED_RELEASE_VERSION
    )
    report = {
        "schema_version": "sat-sim.source-package-self-check.v1",
        "ok": not missing and not forbidden and identity_ok,
        "release_id": manifest.get("release_id"),
        "release_version": manifest.get("release_version"),
        "identity_ok": identity_ok,
        "required_file_count": len(required),
        "missing": missing,
        "forbidden": sorted(set(forbidden)),
        "release_manifest_sha256": _sha256(manifest_path),
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
