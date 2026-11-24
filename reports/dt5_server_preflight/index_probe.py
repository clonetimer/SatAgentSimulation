"""Measure sparse offset lookup on the fixed, chronologically sorted DT-5 fixture."""
import argparse
from bisect import bisect_left
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from sat_sim.telemetry_overview import read_telemetry_overview


def measure(root):
    source = root / "results/telemetry.jsonl"
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert digest == "e0ac5748389b5ee2520d90b6cd0c5d4819b93bf2b64f7168a83e253004ed3e39"
    times, offsets = [], []
    previous = float("-inf")
    offset = 0
    start = perf_counter()
    with source.open("rb") as stream:
        for i, line in enumerate(stream):
            timestamp = json.loads(line)["time_s"]
            assert timestamp >= previous
            previous = timestamp
            if i % 1024 == 0:
                times.append(timestamp)
                offsets.append(offset)
            offset += len(line)
    build_s = perf_counter() - start
    cases = []
    for low, high in [(0, 10), (102.4, 103), (50000, 50010), (70000.01, 70000.02), (99989.9, 100000)]:
        start = perf_counter()
        reference = read_telemetry_overview(root, start_s=low, end_s=high)["rows"]
        scan_s = perf_counter() - start
        samples = []
        for _ in range(5):
            start = perf_counter()
            with source.open("rb") as stream:
                stream.seek(offsets[max(0, bisect_left(times, low) - 1)])
                rows = []
                for line in stream:
                    row = json.loads(line)
                    if row["time_s"] > high:
                        break
                    if row["time_s"] >= low:
                        rows.append(row)
            samples.append(perf_counter() - start)
            assert rows == reference
        cases.append(dict(start_s=low, end_s=high, rows=len(reference), scan_s=scan_s,
                          indexed_s=samples, indexed_median_s=median(samples)))
    return dict(source_sha256=digest, stride=1024, entries=len(times), build_s=build_s, cases=cases)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(measure(args.root), indent=2) + "\n", encoding="utf-8")
