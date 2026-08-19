from __future__ import annotations

import threading
from typing import Any

from sat_sim.interactive.performance import (
    benchmark_capacity,
    benchmark_command_latency,
    benchmark_long_sim,
    benchmark_quantum,
    benchmark_slow_client,
    benchmark_telemetry,
    percentile,
    run_performance_benchmark,
)


class ProbeRuntime:
    def __init__(self) -> None:
        self._time = 0.0
        self.owner_threads: set[int] = set()
        self.closed = False

    @property
    def current_time_s(self) -> float:
        return self._time

    def prepare(self) -> None:
        self.owner_threads.add(threading.get_ident())

    def advance_to(self, stop_time_s: float) -> tuple[dict[str, Any], ...]:
        self.owner_threads.add(threading.get_ident())
        self._time = stop_time_s
        return ({"time_s": stop_time_s, "value": stop_time_s},)

    def apply_command(self, command) -> None:
        self.owner_threads.add(threading.get_ident())

    def read_delta(self) -> tuple[dict[str, Any], ...]:
        return ()

    def finalize(self) -> dict[str, Any]:
        self.closed = True
        return {"final_sim_time_s": self._time}

    def abort(self) -> None:
        self.closed = True


def _factory(duration_s: float, quantum_s: float) -> ProbeRuntime:
    assert duration_s > 0.0
    assert quantum_s > 0.0
    return ProbeRuntime()


def _budget() -> dict:
    return {
        "schema_version": "interactive-performance-budget.v1",
        "claim": "soft_realtime",
        "rates": [0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0],
        "mvp": {
            "single_session_drift_p95_ms_at_1x_30min": 200,
            "command_receive_to_queue_p95_ms": 100,
            "command_effect_max_quanta": 2,
            "telemetry_delivery_p95_ms_at_10hz": 200,
            "telemetry_aggregate_min_frames_per_second": 100,
            "quantum_compute_p95_max_utilization_at_1x": 1.0,
            "rate_min_attainment_ratio": 0.85,
            "max_process_rss_bytes_at_concurrency_4": 2147483648,
            "max_rss_growth_bytes_at_4h_sim": 1073741824,
            "max_terminal_thread_growth": 0,
            "max_terminal_fd_growth": 0,
            "formal_drift_wall_duration_s": 1800,
            "formal_long_sim_duration_s": 14400,
        },
    }


def test_percentile_interpolates_and_validates_inputs() -> None:
    assert percentile([0.0, 10.0], 95.0) == 9.5


def test_individual_benchmarks_measure_latency_backpressure_capacity_and_resources() -> None:
    assert benchmark_quantum(_factory, samples=3)["status"] == "PASS"
    assert benchmark_command_latency(_factory, samples=5, budget_ms=100.0, effect_max_quanta=2)["status"] == "PASS"
    assert benchmark_telemetry(duration_s=0.1, delivery_budget_ms=200.0)["status"] == "PASS"
    assert benchmark_slow_client(frames=20)["status"] == "PASS"
    assert benchmark_capacity(_factory, duration_s=0.2, quantum_s=0.1)["status"] == "PASS"
    assert benchmark_long_sim(_factory, sim_duration_s=2.0, quantum_s=0.1)["status"] == "PASS"


def test_ci_profile_never_closes_formal_duration_gates() -> None:
    report = run_performance_benchmark(_factory, _budget(), formal=False)
    assert report["qualification_checks_passed"] is True
    assert report["formal_passed"] is False
    assert report["status"] == "BLOCKED"
    assert set(report["remaining_blockers"]) == {"drift_1x", "long_sim"}
    assert report["checks"]["drift_1x"]["duration_gate_s"] == 1800.0
    assert report["checks"]["long_sim"]["duration_gate_s"] == 14400.0
