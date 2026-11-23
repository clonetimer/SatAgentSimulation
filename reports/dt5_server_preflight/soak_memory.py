"""Add memory sampling to the pinned continuous-soak test without changing the runtime."""
import hashlib
import json
import sys
import time
from pathlib import Path

import psutil


def measure(root, workspace, output):
    source = root / "tests/interactive/test_continuous_soak.py"
    data = source.read_bytes()
    samples = []
    process = psutil.Process()
    started = time.perf_counter()
    threshold = 0

    def mark(stage, session):
        mem = process.memory_info()
        samples.append({"stage": stage, "elapsed_s":time.perf_counter()-started,
                        "sim_time_s":session.current_time_s, "rss":mem.rss,
                        "private_bytes":getattr(mem,"private",None)})
        output.write_text(json.dumps({"source_sha256":hashlib.sha256(data).hexdigest(),
                                     "samples":samples}, indent=2)+"\n")

    def progress(session):
        nonlocal threshold
        if session.current_time_s >= threshold:
            mark("running", session)
            threshold = (int(session.current_time_s)//300+1)*300

    text = data.decode("utf-8")
    for anchor, replacement in [
        ("        session.prepare()", '        mark("before_prepare", session)\n        session.prepare()\n        mark("prepared", session)'),
        ("        session.start()", '        session.start()\n        mark("started", session)'),
        ("            time.sleep(0.02)", "            progress(session)\n            time.sleep(0.02)"),
        ("        frames = session.telemetry", '        mark("paused_before_history_read", session)\n        frames = session.telemetry'),
        ("        summary = session.stop()", '        mark("history_read", session)\n        summary = session.stop()\n        mark("stop_returned", session)'),
        ("        after_archive_rss =", '        mark("archive_verified", session)\n        after_archive_rss ='),
    ]:
        assert text.count(anchor) == 1, anchor
        text = text.replace(anchor,replacement)
    context = {"__name__":"soak_sampler", "__file__":str(source), "mark":mark,"progress":progress}
    exec(compile(text,str(source),"exec"),context)
    context["test_accelerated_continuous_180_seconds_tc_tm_beyond_initial_horizon"](workspace)
    mark_state = {"source_sha256":hashlib.sha256(data).hexdigest(),"completed":True,"samples":samples}
    output.write_text(json.dumps(mark_state,indent=2)+"\n")


if __name__ == "__main__":
    measure(*map(Path,sys.argv[1:]))
