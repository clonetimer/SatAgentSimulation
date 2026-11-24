from __future__ import annotations

import hashlib
import json
import math
import time
import tracemalloc
from pathlib import Path

import psutil

from sat_sim.telemetry_overview import read_telemetry_overview


root = Path(".tmp/million_fixture")
source = root / "results" / "telemetry.jsonl"
source.parent.mkdir(parents=True, exist_ok=True)
if not source.is_file():
    start = time.perf_counter()
    with source.open("w", encoding="utf-8") as out:
        for i in range(1_000_000):
            angle = i * 0.001
            out.write(json.dumps({
                "time_s": i * .1,
                "sample_index": i,
                "orbit.position_m_x": 7_000_000. * math.cos(angle),
                "orbit.position_m_y": 7_000_000. * math.sin(angle),
                "orbit.position_m_z": 100_000. * math.sin(angle / 4),
            }, allow_nan=False, separators=(",", ":")) + "\n")
    print(f"fixture_write_s={time.perf_counter() - start:.3f}", flush=True)

digest = hashlib.file_digest(source.open("rb"), "sha256").hexdigest()
process = psutil.Process()
rss_before = process.memory_info().rss
tracemalloc.start()
start = time.perf_counter()
overview = read_telemetry_overview(root, max_points=10_000, fields=(
    "orbit.position_m_x", "orbit.position_m_y", "orbit.position_m_z",
))
elapsed = time.perf_counter() - start
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
indices = [row["sample_index"] for row in overview["rows"]]
assert overview["total"] == 1_000_000
assert overview["sampled"] is True
assert overview["count"] <= 10_000
assert indices == sorted(set(indices))
assert indices[0] == 0 and indices[-1] == 999_999
report = {
    "source_kind": "synthetic_orbit_fixture_not_native_physics",
    "rows": overview["total"], "selected": overview["count"],
    "source_bytes": source.stat().st_size, "source_sha256": digest,
    "overview_s": elapsed,
    "rss_before_bytes": rss_before,
    "rss_after_bytes": process.memory_info().rss,
    "tracemalloc_peak_bytes": peak,
    "first_index": indices[0], "last_index": indices[-1],
    "sampling_policy": overview["sampling_policy"],
}
print(json.dumps(report, indent=2), flush=True)

