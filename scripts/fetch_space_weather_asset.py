#!/usr/bin/env python3
"""Stage an offline CelesTrak space-weather snapshot for Basilisk NRLMSISE-00.

The runtime never downloads or refreshes this asset. This setup command
downloads SW-Last5Years.csv, validates the observed rows consumed by Basilisk
SpaceWeatherData, and writes a SHA-256 provenance manifest.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sat_sim.bsk_engine.space_weather import inspect_space_weather_file  # noqa: E402

SOURCE_URL = "https://celestrak.org/SpaceData/SW-Last5Years.csv"
DEFAULT_OUTPUT = ROOT / "third_party" / "space_weather" / "SW-Last5Years.csv"


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "SatAgentSimulation-space-weather-setup/1",
            "Accept": "text/csv,*/*",
            "Accept-Encoding": "identity",
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        raw = response.read(16 * 1024 * 1024 + 1)
    if not raw or len(raw) > 16 * 1024 * 1024:
        raise ValueError("SPACE_WEATHER_DOWNLOAD_SIZE_INVALID")
    staged = destination.with_name(destination.name + ".part")
    try:
        staged.write_bytes(raw)
        # Validate before replacing the last known good snapshot.
        inspect_space_weather_file(staged)
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--url", default=SOURCE_URL)
    parser.add_argument("--refresh", action="store_true", help="download a new snapshot even when one exists")
    parser.add_argument("--offline", action="store_true", help="validate the existing snapshot without network access")
    args = parser.parse_args()

    destination = args.output.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest_path = destination.with_name("space_weather_manifest.json")
    old_manifest: dict = {}
    if manifest_path.is_file():
        try:
            old_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            old_manifest = {}

    downloaded = args.refresh or not destination.is_file()
    if downloaded:
        if args.offline:
            raise FileNotFoundError(f"SPACE_WEATHER_ASSET_MISSING:{destination}")
        _download(args.url, destination)

    evidence = inspect_space_weather_file(destination)
    old_asset = old_manifest.get("asset", {}) if isinstance(old_manifest, dict) else {}
    same_snapshot = old_asset.get("sha256") == evidence["sha256"]
    retrieved_at = (
        datetime.now(timezone.utc).isoformat()
        if downloaded
        else (old_manifest.get("retrieved_at_utc") if same_snapshot else None)
    )
    manifest = {
        "schema_version": "sat-sim-space-weather-asset.v1",
        "status": "PASS",
        "source_url": args.url if downloaded else old_manifest.get("source_url", args.url),
        "retrieved_at_utc": retrieved_at,
        "retrieval_time_unknown": retrieved_at is None,
        "validated_at_utc": datetime.now(timezone.utc).isoformat(),
        "asset": evidence,
        "runtime_network_required": False,
        "scope": "CelesTrak observed space-weather indices for Basilisk NRLMSISE-00; not spacecraft calibration or flight validation.",
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "PASS",
        "asset": str(destination),
        "sha256": evidence["sha256"],
        "valid_date_start": evidence["valid_date_start"],
        "valid_date_end": evidence["valid_date_end"],
        "valid_row_count": evidence["valid_row_count"],
        "manifest": str(manifest_path),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
