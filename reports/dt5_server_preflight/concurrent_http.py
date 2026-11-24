"""Run from the indexed service checkout with PYTHONPATH=src and the million_fixture."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import socket
from threading import Barrier, Thread
from time import monotonic, perf_counter, sleep
from urllib.request import build_opener, ProxyHandler

import uvicorn
from sat_sim.api import create_app


def measure(runs_root):
    fixture = runs_root / "million_fixture/results/telemetry.jsonl"
    with fixture.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    assert digest == "e0ac5748389b5ee2520d90b6cd0c5d4819b93bf2b64f7168a83e253004ed3e39"
    app = create_app(runs_root=runs_root, artifacts_root=runs_root / "concurrency-artifacts",
                     embedded_worker=False, auth_mode="disabled")
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        thread = Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = monotonic() + 20
            while not server.started:
                if not thread.is_alive() or monotonic() > deadline:
                    raise RuntimeError("Server startup failed")
                sleep(.05)

            def worker(client_id, barrier, count):
                opener = build_opener(ProxyHandler({}))
                records = []
                barrier.wait(timeout=20)
                for i in range(count):
                    start_s = (10000 * client_id + 131 * i) % 99000
                    url = (f"http://127.0.0.1:{port}/runs/million_fixture/telemetry/overview"
                           f"?start_s={start_s}&end_s={start_s + 10}&max_points=10000")
                    start = perf_counter()
                    with opener.open(url, timeout=120) as response:
                        body = response.read()
                    latency = perf_counter() - start
                    data = json.loads(body)
                    assert data["total"] == data["count"] == 101
                    assert [r["sample_index"] for r in data["rows"]] == list(range(start_s*10, start_s*10+101))
                    records.append(dict(client=client_id, start_s=start_s, seconds=latency))
                return records

            phases = []
            for label, clients, count in [("first_simultaneous",4,1), ("warm_single",1,100),
                                          ("warm_four",4,100)]:
                barrier = Barrier(clients)
                start = perf_counter()
                with ThreadPoolExecutor(max_workers=clients) as pool:
                    futures = [pool.submit(worker, j, barrier, count) for j in range(clients)]
                    raw = [record for future in futures for record in future.result()]
                wall = perf_counter() - start
                values = sorted(r["seconds"] for r in raw)
                quantiles = {f"p{q}": values[math.ceil(len(values)*q/100)-1] for q in (50,95,99)}
                phases.append(dict(label=label, requests=len(raw), wall_s=wall, nearest_rank_s=quantiles,
                                   records=raw))
                print(label, len(raw), round(wall,3), quantiles, flush=True)
            return dict(source_sha256=digest, server="single uvicorn process, loopback HTTP", phases=phases)
        finally:
            server.should_exit = True
            thread.join(timeout=20)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs_root", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(measure(args.runs_root), indent=2), encoding="utf-8")
