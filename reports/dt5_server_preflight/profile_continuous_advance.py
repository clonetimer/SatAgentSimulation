"""Profile continuous simulation phases with a separate 360 s advancement budget.

Pass the source checkout, a fresh workspace parent and an output JSON path.
This diagnostic retains the original 15 s seal wait. Use the original pytest
entry point to assess the combined advancement and sealing deadlines.
"""
import argparse
import json
import os
import time
from functools import wraps
from pathlib import Path
from unittest.mock import patch
from contextlib import ExitStack

from sat_sim.interactive.manager import InteractiveSession
from sat_sim.interactive.unified_runtime import UnifiedPersistentRuntime
from sat_sim.interactive.workspace import SessionWorkspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkout", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    source_file = args.checkout / "tests/interactive/test_continuous_soak.py"
    source = source_file.read_text(encoding="utf-8")
    budget = "max(90.0, target_s / 20.0)"
    if source.count(budget) != 1 or "assert session.wait(15.0)" not in source:
        raise ValueError("Unexpected soak test source")
    source = source.replace(budget, "360.0")
    stats = {}

    def timed(function):
        stat = stats[function.__name__] = {"calls": 0, "seconds": 0}

        @wraps(function)
        def observe(*args, **kwargs):
            start = time.perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                stat["calls"] += 1
                stat["seconds"] += time.perf_counter() - start
        return observe

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, SAT_SIM_CONTINUOUS_SOAK_TARGET_S="3600"))
        for owner, name in (
            (os, "fsync"), (SessionWorkspace, "append_telemetry"),
            (UnifiedPersistentRuntime, "advance_to"), (InteractiveSession, "_advance"),
        ):
            stack.enter_context(patch.object(owner, name, timed(getattr(owner, name))))
        namespace = {"__name__": "phase_probe", "__file__": str(source_file)}
        exec(compile(source, str(source_file), "exec"), namespace)
        try:
            namespace["test_accelerated_continuous_180_seconds_tc_tm_beyond_initial_horizon"](args.workspace)
        finally:
            args.output.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
