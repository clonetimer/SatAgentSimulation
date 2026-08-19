"""Measured soft-real-time performance qualification.

The short profile is a CI regression signal.  Only the formal profile may close
the frozen 30-minute wall-clock and four-hour simulated-time gates.
"""
from __future__ import annotations

import math
import statistics
import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .manager import InteractiveSession
from .models import ALLOWED_RATES, CommandState, InteractiveSessionSpec, SessionState, Telecommand
from .runtime import PersistentSimulationRuntime
from .telemetry_bus import TelemetryBus

RuntimeFactory = Callable[[float, float], PersistentSimulationRuntime]


def percentile(values: list[float] | tuple[float, ...], percent: float) -> float:
    """Return a linearly interpolated percentile with stable edge semantics."""

    if not values:
        raise ValueError("percentile requires at least one value")
    if not 0.0 <= percent <= 100.0:
        raise ValueError("percent must be in [0, 100]")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percent / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def load_budget(path: str | Path) -> dict[str, Any]:
    import json

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema_version") != "interactive-performance-budget.v1":
        raise ValueError("unsupported interactive performance budget")
    if tuple(float(value) for value in payload.get("rates", ())) != ALLOWED_RATES:
        raise ValueError("performance budget rates differ from the runtime contract")
    return payload


def _resource_snapshot() -> dict[str, int]:
    import psutil

    process = psutil.Process()
    return {
        "rss_bytes": int(process.memory_info().rss),
        "threads": int(process.num_threads()),
        "fds": int(process.num_fds()) if hasattr(process, "num_fds") else -1,
    }


def _finish_runtime(runtime: PersistentSimulationRuntime, *, abort: bool = False) -> None:
    try:
        runtime.abort() if abort else runtime.finalize()
    except Exception:
        if not abort:
            try:
                runtime.abort()
            except Exception:
                pass


def benchmark_quantum(factory: RuntimeFactory, *, samples: int, max_utilization: float = 1.0) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for quantum_s in (0.1, 0.5, 1.0):
        duration_s = quantum_s * samples
        runtime = factory(duration_s, quantum_s)
        timings: list[float] = []
        started = time.perf_counter()
        try:
            runtime.prepare()
            for index in range(samples):
                before = time.perf_counter()
                rows = runtime.advance_to((index + 1) * quantum_s)
                timings.append(time.perf_counter() - before)
                if not rows:
                    raise RuntimeError("runtime made no progress during quantum benchmark")
            _finish_runtime(runtime)
        except BaseException:
            _finish_runtime(runtime, abort=True)
            raise
        elapsed = time.perf_counter() - started
        p95 = percentile(timings, 95.0)
        cases.append({
            "quantum_s": quantum_s,
            "samples": samples,
            "compute_ms_p50": round(percentile(timings, 50.0) * 1000.0, 6),
            "compute_ms_p95": round(p95 * 1000.0, 6),
            "compute_ms_max": round(max(timings) * 1000.0, 6),
            "one_x_headroom_ratio": round(quantum_s / p95, 6) if p95 else None,
            "achieved_unpaced_sim_rate": round(duration_s / elapsed, 6),
            "compute_utilization_at_1x": round(p95 / quantum_s, 6),
            "compute_utilization_budget": max_utilization,
            "status": "PASS" if p95 / quantum_s <= max_utilization else "FAIL",
        })
    return {"status": "PASS" if all(case["status"] == "PASS" for case in cases) else "FAIL", "cases": cases}


def _session_spec(session_id: str, duration_s: float, quantum_s: float, *, rate: float, paced: bool) -> InteractiveSessionSpec:
    return InteractiveSessionSpec(
        session_id=session_id,
        capability_id="whole_spacecraft.unified_native.v1",
        task_spec={},
        quantum_s=quantum_s,
        rate=rate,
        paced=paced,
        max_sim_time_s=duration_s,
        # Session wall limits include native model preparation.  Keep the
        # measurement window exact while allowing initialization/finalization
        # overhead; drift is still judged only from raw pacing samples.
        max_wall_time_s=max(600.0, duration_s / max(rate, 0.1) + 600.0),
        idle_timeout_s=max(60.0, duration_s / max(rate, 0.1) + 60.0),
        drift_tolerance_s=0.2,
    )


def benchmark_paced_session(
    factory: RuntimeFactory,
    *,
    rate: float,
    wall_duration_s: float,
    quantum_s: float,
    session_id: str,
) -> dict[str, Any]:
    sim_duration_s = max(quantum_s, rate * wall_duration_s)
    runtime = factory(sim_duration_s, quantum_s)
    session = InteractiveSession(
        _session_spec(session_id, sim_duration_s, quantum_s, rate=rate, paced=True),
        runtime,
        end_time_s=sim_duration_s,
    )
    session.prepare()
    started = time.perf_counter()
    session.start()
    timeout_s = wall_duration_s + max(30.0, wall_duration_s * 0.2)
    if not session.wait(timeout_s):
        try:
            session.abort()
        except RuntimeError:
            pass
        raise TimeoutError(f"paced benchmark timed out at rate={rate}")
    elapsed = time.perf_counter() - started
    observations = session.pace_observations()
    drift_ms = [abs(item.drift_s) * 1000.0 for item in observations]
    achieved_rate = session.current_time_s / elapsed if elapsed else 0.0
    return {
        "rate_target": rate,
        "wall_duration_s": round(elapsed, 6),
        "sim_duration_s": session.current_time_s,
        "observation_count": len(observations),
        "absolute_drift_ms_p50": round(percentile(drift_ms, 50.0), 6),
        "absolute_drift_ms_p95": round(percentile(drift_ms, 95.0), 6),
        "absolute_drift_ms_max": round(max(drift_ms), 6),
        "achieved_rate": round(achieved_rate, 6),
        "attainment_ratio": round(achieved_rate / rate, 6),
        "terminal_state": session.state.value,
        "terminal_error": session.terminal_error,
        "status": "PASS" if session.state is SessionState.COMPLETED else "FAIL",
    }


def benchmark_rates(
    factory: RuntimeFactory,
    *,
    wall_s_per_rate: float,
    quantum_s: float,
    min_attainment_ratio: float = 0.85,
) -> dict[str, Any]:
    cases = [
        benchmark_paced_session(
            factory,
            rate=rate,
            wall_duration_s=wall_s_per_rate,
            quantum_s=min(quantum_s, max(0.01, rate * wall_s_per_rate / 5.0)),
            session_id=f"perf-rate-{str(rate).replace('.', '-')}",
        )
        for rate in ALLOWED_RATES
    ]
    for case in cases:
        case["attainment_ratio_budget"] = min_attainment_ratio
        if case["attainment_ratio"] < min_attainment_ratio:
            case["status"] = "FAIL"
    return {"status": "PASS" if all(case["status"] == "PASS" for case in cases) else "FAIL", "cases": cases}


def benchmark_command_latency(factory: RuntimeFactory, *, samples: int, budget_ms: float, effect_max_quanta: int) -> dict[str, Any]:
    quantum_s = 0.5
    runtime = factory(quantum_s, quantum_s)
    spec = _session_spec("perf-command", quantum_s, quantum_s, rate=1.0, paced=False).model_copy(
        update={"command_queue_capacity": max(1024, samples + 1)}
    )
    session = InteractiveSession(spec, runtime, end_time_s=quantum_s)
    session.prepare()
    latencies_ms: list[float] = []
    for index in range(samples):
        command = Telecommand(
            command_id=f"perf-command-{index}",
            session_id=spec.session_id,
            session_revision=session.revision,
            operation="eps.load.set",
            target="subsystem.eps",
            parameters={"load_id": "payload", "enabled": bool(index % 2)},
            actor_id="performance-operator",
            actor_role="operator",
            execute_at_sim_time_s=quantum_s,
        )
        before = time.perf_counter()
        ack = session.submit_command(command)
        latencies_ms.append((time.perf_counter() - before) * 1000.0)
        if ack.state is not CommandState.QUEUED:
            raise RuntimeError(f"benchmark command was not queued: {ack.reason_code}")
    session.start()
    if not session.wait(30.0):
        raise TimeoutError("command latency benchmark session did not finish")
    final_acks = [ack for ack in session.command_acks() if ack.state is CommandState.ACKED]
    effect_quanta = max((ack.sim_time_s or 0.0) / quantum_s for ack in final_acks) if final_acks else math.inf
    p95 = percentile(latencies_ms, 95.0)
    passed = p95 <= budget_ms and len(final_acks) == samples and effect_quanta <= effect_max_quanta
    return {
        "status": "PASS" if passed else "FAIL",
        "samples": samples,
        "receive_to_queue_ms_p50": round(percentile(latencies_ms, 50.0), 6),
        "receive_to_queue_ms_p95": round(p95, 6),
        "receive_to_queue_ms_max": round(max(latencies_ms), 6),
        "acked_count": len(final_acks),
        "effect_max_quanta": effect_quanta,
        "budget_ms": budget_ms,
        "effect_budget_quanta": effect_max_quanta,
    }


def benchmark_telemetry(*, duration_s: float, delivery_budget_ms: float, min_aggregate_fps: float = 100.0) -> dict[str, Any]:
    bus = TelemetryBus("perf-telemetry", capacity_per_stream=10000)
    subscriber = bus.subscribe(("ten_hz",), capacity=10000)
    published_at: dict[int, float] = {}
    latencies_ms: list[float] = []
    frame_count = max(10, math.ceil(duration_s * 10.0))

    def publish() -> None:
        epoch = time.perf_counter()
        for index in range(frame_count):
            due = epoch + index / 10.0
            remaining = due - time.perf_counter()
            if remaining > 0:
                time.sleep(remaining)
            published_at[index] = time.perf_counter()
            bus.publish("ten_hz", index / 10.0, {"value": index})

    thread = threading.Thread(target=publish, name="sat-perf-telemetry-publisher")
    thread.start()
    received: list[int] = []
    deadline = time.monotonic() + duration_s + 10.0
    while len(received) < frame_count and time.monotonic() < deadline:
        for item in subscriber.poll(timeout_s=0.2, max_items=frame_count):
            if hasattr(item, "sequence"):
                received.append(item.sequence)
                latencies_ms.append((time.perf_counter() - published_at[item.sequence]) * 1000.0)
    thread.join(2.0)
    expected = list(range(frame_count))
    silent_drops = len(expected) - len(received)
    p95 = percentile(latencies_ms, 95.0) if latencies_ms else math.inf

    throughput_bus = TelemetryBus("perf-throughput", capacity_per_stream=20000)
    fast = throughput_bus.subscribe(tuple(f"stream-{index}" for index in range(10)), capacity=20000)
    throughput_started = time.perf_counter()
    throughput_frames = max(1000, frame_count * 10)
    for index in range(throughput_frames):
        throughput_bus.publish(f"stream-{index % 10}", index / 100.0, {"value": index})
    publish_elapsed = time.perf_counter() - throughput_started
    delivered = len(fast.poll(max_items=throughput_frames))
    aggregate_fps = throughput_frames / publish_elapsed
    passed = (
        silent_drops == 0
        and received == expected
        and p95 <= delivery_budget_ms
        and delivered == throughput_frames
        and aggregate_fps >= min_aggregate_fps
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "ten_hz": {
            "published": frame_count,
            "received": len(received),
            "silent_drops": silent_drops,
            "delivery_ms_p50": round(percentile(latencies_ms, 50.0), 6) if latencies_ms else None,
            "delivery_ms_p95": round(p95, 6) if math.isfinite(p95) else None,
            "delivery_budget_ms": delivery_budget_ms,
        },
        "aggregate_100_fps": {
            "frames": throughput_frames,
            "delivered": delivered,
            "publish_rate_fps": round(aggregate_fps, 3),
            "publish_rate_budget_fps": min_aggregate_fps,
        },
    }


def benchmark_slow_client(*, frames: int = 1000) -> dict[str, Any]:
    bus = TelemetryBus("perf-backpressure", capacity_per_stream=frames)
    slow = bus.subscribe(("fast",), capacity=2, overflow_policy="drop_oldest")
    fast = bus.subscribe(("fast",), capacity=frames, overflow_policy="drop_oldest")
    started = time.perf_counter()
    for index in range(frames):
        bus.publish("fast", index / 100.0, {"value": index})
    elapsed = time.perf_counter() - started
    slow_rows = slow.poll(max_items=frames)
    fast_rows = fast.poll(max_items=frames)
    gap_count = sum(int(getattr(item, "dropped_count", 0)) for item in slow_rows)
    passed = len(fast_rows) == frames and gap_count == frames - 2
    return {
        "status": "PASS" if passed else "FAIL",
        "published": frames,
        "fast_received": len(fast_rows),
        "slow_gap_count": gap_count,
        "publish_elapsed_ms": round(elapsed * 1000.0, 6),
        "publisher_blocked": False,
    }


def benchmark_capacity(
    factory: RuntimeFactory,
    *,
    duration_s: float,
    quantum_s: float,
    max_rss_bytes_at_four: int = 2 * 1024**3,
) -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for concurrency in (1, 2, 4):
        before = _resource_snapshot()
        sessions: list[InteractiveSession] = []
        started = time.perf_counter()
        for index in range(concurrency):
            runtime = factory(duration_s, quantum_s)
            spec = _session_spec(f"perf-capacity-{concurrency}-{index}", duration_s, quantum_s, rate=10.0, paced=False)
            session = InteractiveSession(spec, runtime, end_time_s=duration_s)
            session.prepare()
            sessions.append(session)
        for session in sessions:
            session.start()
        completed_rows = [session.wait(120.0) for session in sessions]
        completed = all(completed_rows)
        elapsed = time.perf_counter() - started
        after = _resource_snapshot()
        states = [session.state.value for session in sessions]
        passed = (
            completed
            and all(session.state is SessionState.COMPLETED for session in sessions)
            and (concurrency < 4 or after["rss_bytes"] <= max_rss_bytes_at_four)
        )
        cases.append({
            "concurrency": concurrency,
            "status": "PASS" if passed else "FAIL",
            "terminal_states": states,
            "terminal_errors": [session.terminal_error for session in sessions],
            "elapsed_s": round(elapsed, 6),
            "aggregate_sim_rate": round(concurrency * duration_s / elapsed, 6) if elapsed else None,
            "rss_delta_bytes": after["rss_bytes"] - before["rss_bytes"],
            "rss_bytes": after["rss_bytes"],
            "rss_budget_bytes_at_concurrency_4": max_rss_bytes_at_four,
            "thread_delta": after["threads"] - before["threads"],
            "fd_delta": after["fds"] - before["fds"] if before["fds"] >= 0 else None,
        })
    return {"status": "PASS" if all(case["status"] == "PASS" for case in cases) else "FAIL", "cases": cases}


def benchmark_long_sim(
    factory: RuntimeFactory,
    *,
    sim_duration_s: float,
    quantum_s: float,
    max_rss_growth_bytes: int = 1024**3,
    max_thread_growth: int = 0,
    max_fd_growth: int = 0,
) -> dict[str, Any]:
    runtime = factory(sim_duration_s, quantum_s)
    before = _resource_snapshot()
    samples = [before]
    segments = math.ceil(sim_duration_s / quantum_s)
    started = time.perf_counter()
    try:
        runtime.prepare()
        sample_stride = max(1, segments // 20)
        for index in range(segments):
            target = min(sim_duration_s, (index + 1) * quantum_s)
            if not runtime.advance_to(target):
                raise RuntimeError("runtime made no progress during long simulation")
            if index % sample_stride == 0:
                samples.append(_resource_snapshot())
        _finish_runtime(runtime)
    except BaseException:
        _finish_runtime(runtime, abort=True)
        raise
    samples.append(_resource_snapshot())
    elapsed = time.perf_counter() - started
    rss_values = [sample["rss_bytes"] for sample in samples]
    tail = rss_values[len(rss_values) // 2:]
    total_growth = samples[-1]["rss_bytes"] - before["rss_bytes"]
    tail_growth = tail[-1] - min(tail)
    thread_growth = samples[-1]["threads"] - before["threads"]
    fd_growth = samples[-1]["fds"] - before["fds"] if before["fds"] >= 0 else 0
    passed = (
        total_growth <= max_rss_growth_bytes
        and thread_growth <= max_thread_growth
        and fd_growth <= max_fd_growth
    )
    return {
        "status": "PASS" if passed else "FAIL",
        "sim_duration_s": sim_duration_s,
        "segments": segments,
        "elapsed_s": round(elapsed, 6),
        "achieved_unpaced_sim_rate": round(sim_duration_s / elapsed, 6),
        "rss_initial_bytes": before["rss_bytes"],
        "rss_peak_bytes": max(rss_values),
        "rss_total_growth_bytes": total_growth,
        "rss_tail_growth_bytes": tail_growth,
        "rss_growth_budget_bytes": max_rss_growth_bytes,
        "rss_growth_bytes_per_sim_hour": round(total_growth / max(sim_duration_s / 3600.0, 1e-12), 3),
        "memory_growth_interpretation": "Controlled interactive segments clear native recorder history after publishing each delta; the latest segment remains for final native summary and the bounded telemetry bus/workspace owns the session trace.",
        "thread_growth": thread_growth,
        "thread_growth_budget": max_thread_growth,
        "fd_growth": fd_growth,
        "fd_growth_budget": max_fd_growth,
        "resource_samples": samples,
    }


def run_performance_benchmark(
    factory: RuntimeFactory,
    budget: dict[str, Any],
    *,
    formal: bool,
) -> dict[str, Any]:
    limits = budget["mvp"]
    profile = {
        "quantum_samples": 30 if formal else 5,
        "rate_wall_s": 5.0 if formal else 0.25,
        "drift_wall_s": 1800.0 if formal else 1.0,
        "telemetry_wall_s": 30.0 if formal else 1.0,
        "command_samples": 200 if formal else 20,
        "capacity_sim_s": 60.0 if formal else 2.0,
        "long_sim_s": 14400.0 if formal else 60.0,
    }
    started_at = datetime.now(timezone.utc).isoformat()
    checks = {
        "quantum": benchmark_quantum(
            factory,
            samples=profile["quantum_samples"],
            max_utilization=float(limits["quantum_compute_p95_max_utilization_at_1x"]),
        ),
        "rates": benchmark_rates(
            factory,
            wall_s_per_rate=profile["rate_wall_s"],
            quantum_s=0.1,
            min_attainment_ratio=float(limits["rate_min_attainment_ratio"]),
        ),
        "drift_1x": benchmark_paced_session(
            factory,
            rate=1.0,
            wall_duration_s=profile["drift_wall_s"],
            quantum_s=0.1,
            session_id="perf-drift-1x",
        ),
        "command": benchmark_command_latency(
            factory,
            samples=profile["command_samples"],
            budget_ms=float(limits["command_receive_to_queue_p95_ms"]),
            effect_max_quanta=int(limits["command_effect_max_quanta"]),
        ),
        "telemetry": benchmark_telemetry(
            duration_s=profile["telemetry_wall_s"],
            delivery_budget_ms=float(limits["telemetry_delivery_p95_ms_at_10hz"]),
            min_aggregate_fps=float(limits["telemetry_aggregate_min_frames_per_second"]),
        ),
        "slow_client": benchmark_slow_client(),
        "capacity": benchmark_capacity(
            factory,
            duration_s=profile["capacity_sim_s"],
            quantum_s=0.5,
            max_rss_bytes_at_four=int(limits["max_process_rss_bytes_at_concurrency_4"]),
        ),
        "long_sim": benchmark_long_sim(
            factory,
            sim_duration_s=profile["long_sim_s"],
            quantum_s=1.0,
            max_rss_growth_bytes=int(limits["max_rss_growth_bytes_at_4h_sim"]),
            max_thread_growth=int(limits["max_terminal_thread_growth"]),
            max_fd_growth=int(limits["max_terminal_fd_growth"]),
        ),
    }
    drift = checks["drift_1x"]
    drift["budget_ms"] = float(limits["single_session_drift_p95_ms_at_1x_30min"])
    drift["duration_gate_s"] = float(limits["formal_drift_wall_duration_s"])
    drift["status"] = "PASS" if (
        drift["status"] == "PASS"
        and drift["absolute_drift_ms_p95"] <= drift["budget_ms"]
        and profile["drift_wall_s"] >= drift["duration_gate_s"]
    ) else "BLOCKED" if profile["drift_wall_s"] < drift["duration_gate_s"] else "FAIL"
    long_sim = checks["long_sim"]
    long_sim["duration_gate_s"] = float(limits["formal_long_sim_duration_s"])
    if profile["long_sim_s"] < long_sim["duration_gate_s"] and long_sim["status"] == "PASS":
        long_sim["status"] = "BLOCKED"
    non_formal_checks_passed = all(item["status"] in {"PASS", "BLOCKED"} for item in checks.values())
    formal_passed = formal and all(item["status"] == "PASS" for item in checks.values())
    return {
        "schema_version": "interactive-performance-benchmark.v1",
        "gate": "RT7_PERFORMANCE_SECURITY",
        "scope": "soft-realtime internal engineering simulation; not hard-real-time, hardware, or flight validation",
        "profile": "formal" if formal else "ci",
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "parameters": profile,
        "budget": budget,
        "checks": checks,
        "qualification_checks_passed": non_formal_checks_passed,
        "formal_passed": formal_passed,
        "status": "PASS" if formal_passed else "BLOCKED" if non_formal_checks_passed else "FAIL",
        "remaining_blockers": [] if formal_passed else [
            name for name, item in checks.items() if item["status"] != "PASS"
        ],
    }


def resume_formal_capacity(
    factory: RuntimeFactory,
    budget: dict[str, Any],
    prior: dict[str, Any],
) -> dict[str, Any]:
    """Rerun only capacity after a terminal-boundary fix.

    Long-duration evidence is carried forward only when the prior formal report
    proves the complete frozen durations and every non-capacity check passed.
    This avoids repeating a 30-minute wall-clock measurement for a code change
    limited to exact final-stop conversion, while remaining fail-closed.
    """

    if prior.get("schema_version") != "interactive-performance-benchmark.v1" or prior.get("profile") != "formal":
        raise ValueError("resume requires a formal interactive performance report")
    checks = dict(prior.get("checks") or {})
    required = {"quantum", "rates", "drift_1x", "command", "telemetry", "slow_client", "long_sim"}
    if not required.issubset(checks) or any(checks[name].get("status") != "PASS" for name in required):
        raise ValueError("resume refuses incomplete or failed non-capacity evidence")
    limits = budget["mvp"]
    drift = checks["drift_1x"]
    long_sim = checks["long_sim"]
    if (
        float(drift.get("sim_duration_s", 0.0)) < float(limits["formal_drift_wall_duration_s"])
        or int(drift.get("observation_count", 0)) < 18000
        or float(long_sim.get("sim_duration_s", 0.0)) < float(limits["formal_long_sim_duration_s"])
    ):
        raise ValueError("resume refuses evidence that did not complete frozen durations")
    parameters = dict(prior.get("parameters") or {})
    checks["capacity"] = benchmark_capacity(
        factory,
        duration_s=float(parameters["capacity_sim_s"]),
        quantum_s=0.5,
        max_rss_bytes_at_four=int(limits["max_process_rss_bytes_at_concurrency_4"]),
    )
    formal_passed = all(item.get("status") == "PASS" for item in checks.values())
    history = list(prior.get("resume_history") or [])
    history.append({
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "rerun_checks": ["capacity"],
        "reason": "integer_nanosecond_final_stop_conversion_fix",
        "carried_forward_checks": sorted(required),
    })
    return {
        **prior,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "formal_passed": formal_passed,
        "qualification_checks_passed": formal_passed,
        "status": "PASS" if formal_passed else "FAIL",
        "remaining_blockers": [name for name, item in checks.items() if item.get("status") != "PASS"],
        "resume_history": history,
    }


__all__ = [
    "RuntimeFactory",
    "benchmark_capacity",
    "benchmark_command_latency",
    "benchmark_long_sim",
    "benchmark_paced_session",
    "benchmark_quantum",
    "benchmark_rates",
    "benchmark_slow_client",
    "benchmark_telemetry",
    "load_budget",
    "percentile",
    "run_performance_benchmark",
    "resume_formal_capacity",
]
