"""Time finalization and sealing on a fresh copy of a completed continuous run.

Run with PYTHONPATH pointing to the chosen checkout's src directory. Pass the
original workspace and a new destination parent. Keep the session directory name
so the workspace identity check also applies to the copy.
"""
import argparse
import json
import shutil
from pathlib import Path
from time import perf_counter
from unittest.mock import patch

from sat_sim.interactive import continuous_seal
from sat_sim.interactive.models import InteractiveSessionSpec
from sat_sim.interactive.workspace import SessionWorkspace


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    parent = args.destination.resolve()
    if parent == source or source in parent.parents:
        parser.error("destination must be outside the source workspace")
    parent.mkdir(parents=True, exist_ok=False)
    root = parent / source.name
    shutil.copytree(source, root, ignore=shutil.ignore_patterns("run_bundle"))
    spec = json.loads((root / "session_spec.json").read_text(encoding="utf-8"))
    workspace = SessionWorkspace(parent, InteractiveSessionSpec.model_validate(spec["session_spec"]), create=False)
    seconds = {}

    def timed(name, function):
        def measure(*args, **kwargs):
            start = perf_counter()
            try:
                return function(*args, **kwargs)
            finally:
                seconds[name] = perf_counter() - start
        return measure

    with (
        patch.object(SessionWorkspace, "inspect", staticmethod(timed("inspect", SessionWorkspace.inspect))),
        patch.object(continuous_seal, "_write_semantic_trace_and_effects",
                     timed("semantic_trace_including_checks", continuous_seal._write_semantic_trace_and_effects)),
        patch.object(continuous_seal, "verify_run_bundle", timed("bundle_check", continuous_seal.verify_run_bundle)),
    ):
        start = perf_counter()
        timed("finalize", workspace.finalize_telemetry)()
        result = timed("seal_total", continuous_seal.seal_continuous_workspace)(root)
        seconds["overall"] = perf_counter() - start
    if not result["ok"]:
        raise RuntimeError(result)
    print(json.dumps(seconds, indent=2))


if __name__ == "__main__":
    main()
