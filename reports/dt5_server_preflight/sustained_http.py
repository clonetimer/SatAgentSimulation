"""Measure validated sliding-window reads and isolated server memory on loopback."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from multiprocessing import get_context
from pathlib import Path
import socket
from threading import Barrier, Event
from time import monotonic, perf_counter
from urllib.request import build_opener, ProxyHandler

import psutil
import uvicorn
from sat_sim.api import create_app


def serve(root, channel, stop):
    import asyncio
    async def run():
        app = create_app(runs_root=root, artifacts_root=root / "concurrency-artifacts",
                         embedded_worker=False, auth_mode="disabled")
        server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            task = asyncio.create_task(server.serve(sockets=[sock]))
            while not server.started:
                if task.done():
                    await task
                    raise RuntimeError("Startup failed")
                await asyncio.sleep(.02)
            channel.send(sock.getsockname()[1])
            while not stop.is_set():
                await asyncio.sleep(.05)
            server.should_exit = True
            await task
    asyncio.run(run())


def measure(root, seconds):
    fixture = root / "million_fixture/results/telemetry.jsonl"
    with fixture.open("rb") as fd:
        digest = hashlib.file_digest(fd, "sha256").hexdigest()
    assert digest == "e0ac5748389b5ee2520d90b6cd0c5d4819b93bf2b64f7168a83e253004ed3e39"
    ctx = get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    stop = ctx.Event()
    child = ctx.Process(target=serve, args=(root, sender, stop))
    child.start()
    sender.close()
    try:
        if not receiver.poll(45):
            raise TimeoutError("Startup timeout")
        port = receiver.recv()
        process = psutil.Process(child.pid)
        started = monotonic()

        def sample():
            mem = process.memory_info()
            cpu = process.cpu_times()
            return dict(t=monotonic()-started, rss=mem.rss, private_bytes=mem.private,
                        cpu_seconds=cpu.user+cpu.system)

        def request(opener, start):
            url = (f"http://127.0.0.1:{port}/runs/million_fixture/telemetry/overview"
                   f"?start_s={start}&end_s={start+10}&max_points=10000")
            t = perf_counter()
            with opener.open(url, timeout=120) as response:
                body = response.read()
            latency = perf_counter()-t
            data = json.loads(body)
            assert data["total"] == data["count"] == 101
            assert [r["sample_index"] for r in data["rows"]] == list(range(10*start,10*start+101))
            return latency

        samples = [sample()]
        cold = request(build_opener(ProxyHandler({})), 0)
        samples.append(sample())
        done = Event()
        barrier = Barrier(5)

        def worker(i):
            opener = build_opener(ProxyHandler({}))
            barrier.wait(timeout=15)
            latencies = []
            while not done.is_set():
                start = (i*19231 + len(latencies)*131) % 99989
                latencies.append(request(opener, start))
            return latencies

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(worker,i) for i in range(4)]
            try:
                barrier.wait(timeout=15)
                begin = monotonic()
                while monotonic()-begin < seconds:
                    done.wait(min(5, max(0, seconds-(monotonic()-begin))))
                    samples.append(sample())
            finally:
                done.set()
            clients = [f.result() for f in futures]
        wall = monotonic()-begin
        values = sorted(x for client in clients for x in client)
        return dict(fixture_sha256=digest, cold_s=cold, wall_s=wall,
                    requests_by_client=[len(c) for c in clients], samples=samples,
                    warm_latency_ms={f"p{p}":1000*values[math.ceil(len(values)*p/100)-1]
                                     for p in (50,95,99)}, max_latency_ms=1000*values[-1])
    finally:
        stop.set()
        child.join(timeout=15)
        if child.is_alive():
            child.terminate()
            child.join()
        receiver.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--seconds", type=int, default=180)
    args = parser.parse_args()
    result = measure(args.root, args.seconds)
    args.output.write_text(json.dumps(result, indent=2)+"\n")
    print(result["requests_by_client"], result["warm_latency_ms"])
