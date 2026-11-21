#!/usr/bin/env python3
"""获取并验真官方 Basilisk 2.11.0 完整 GGM03S 球谐系数。

用途：仅在安装/CI 资产准备阶段获取官方原始 GGM03S，运行时不联网。
参数：--root 指定资产根目录，--offline 只验证已存在的完整原始文件。
输出：固定 Git blob、文件长度、SHA-256、系数头及来源审计 JSON。
默认 J2-only 资产/物理模型不变。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sat_sim.bsk_engine.gravity_harmonics_asset import (  # noqa: E402
    SOURCE_SIZE_BYTES, SOURCE_URL, inspect_ggm03s,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "third_party")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    destination = args.root.resolve() / "gravity" / "GGM03S.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists() and not args.offline:
        staged = destination.with_suffix(".txt.part")
        try:
            request = Request(SOURCE_URL, headers={
                "User-Agent": "SatAgentSimulation-DT1-GGM03S/1",
                "Accept-Encoding": "identity",
            })
            with urlopen(request, timeout=90) as response:
                data = response.read(SOURCE_SIZE_BYTES + 1)
            if len(data) != SOURCE_SIZE_BYTES:
                raise ValueError("NATIVE_GGM03S_DOWNLOAD_SIZE_MISMATCH")
            staged.write_bytes(data)
            inspect_ggm03s(staged)
            os.replace(staged, destination)
        finally:
            staged.unlink(missing_ok=True)
    record = inspect_ggm03s(destination)
    manifest = {
        "schema_version": "sat-sim.ggm03s-full-provenance.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_scope": "official Basilisk 2.11.0 GGM03S; not flight calibration",
        "runtime_network_required": False,
        "gravity": record,
    }
    manifest_path = destination.with_suffix(".provenance.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print("DT1_GGM03S_PINNED_ASSET " + json.dumps(record, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
