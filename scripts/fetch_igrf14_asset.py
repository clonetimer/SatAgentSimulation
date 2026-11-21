#!/usr/bin/env python3
"""Prepare immutable IAGA IGRF-14 coefficients for the Basilisk MAG evaluator.

用途：显式安装阶段取得 IAGA IGRF14 原始文件并生成兼容 Basilisk 的 COF。
参数：--root 资产根目录；--offline 仅核验已有文件且不访问网络。
输出：原始文件、确定性衍生 COF 及 SHA-256 / 来源审计清单；运行时不下载。
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
from sat_sim.bsk_engine.igrf14_asset import (  # noqa: E402
    COF_NAME, SOURCE_BYTES, SOURCE_NAME, SOURCE_URL,
    parse_igrf14, render_igrf14_cof, verify_igrf14_assets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "third_party")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    folder = args.root.resolve() / "igrf"
    folder.mkdir(parents=True, exist_ok=True)
    source = folder / SOURCE_NAME
    cof = folder / COF_NAME
    if not source.exists():
        if args.offline:
            raise FileNotFoundError("IGRF14_PINNED_SOURCE_OR_COF_MISSING")
        staging = source.with_suffix(source.suffix + ".part")
        try:
            request = Request(SOURCE_URL, headers={
                "User-Agent": "SatAgentSimulation-IGRF14-asset-setup/1",
                "Accept-Encoding": "identity",
            })
            with urlopen(request, timeout=60) as response:
                content = response.read(SOURCE_BYTES + 1)
            parse_igrf14(content)
            staging.write_bytes(content)
            os.replace(staging, source)
        finally:
            staging.unlink(missing_ok=True)
    # Never overwrite a possibly altered derived file in offline verification.
    expected, _ = render_igrf14_cof(source.read_bytes())
    if not cof.exists():
        if args.offline:
            raise FileNotFoundError("IGRF14_PINNED_SOURCE_OR_COF_MISSING")
        temporary = cof.with_suffix(".COF.part")
        try:
            temporary.write_bytes(expected)
            os.replace(temporary, cof)
        finally:
            temporary.unlink(missing_ok=True)
    evidence = verify_igrf14_assets(folder)
    manifest = {
        "schema_version": "sat-sim.igrf14-coefficient-provenance.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_scope": "IAGA_VMOD_IGRF14_2025_main_2025_2030_SV",
        "runtime_network_required": False,
        "geomagnetic_model": evidence,
        "flight_validation": False,
    }
    (folder / "IGRF14-provenance.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print("DT1_IGRF14_PINNED_ASSET " + json.dumps(evidence, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
