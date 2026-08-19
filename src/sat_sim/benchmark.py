"""Performance benchmarking utilities for simulation components.

This module provides functions to measure and report performance of:
- Battery simulation
- Thermal node simulation
- Solar panel simulation
- Combined simulations

Results include execution time, steps per second, and memory usage.
"""
from __future__ import annotations

import time
from typing import Dict, Any, List, Optional, Callable, Tuple
from dataclasses import dataclass

from components.dynamic_models import (
    step_battery,
    ThermalNodeConfig,
    ThermalNodeState,
    step_thermal_node,
    SolarPanelConfig,
    SolarPanelState,
    step_solar_tracking,
)
from components.battery import BatteryConfig, BatteryState


@dataclass(frozen=True)
class BenchmarkResult:
    """Result of a benchmark run.
    
    Attributes:
        name: Benchmark name
        steps: Number of simulation steps
        duration_s: Total execution time in seconds
        steps_per_second: Steps per second
        avg_time_per_step_us: Average time per step in microseconds
        memory_mb: Approximate memory usage in MB
    """
    name: str
    steps: int
    duration_s: float
    steps_per_second: float
    avg_time_per_step_us: float
    memory_mb: float = 0.0


def _get_memory_usage() -> float:
    """Get approximate memory usage in MB."""
    try:
        import psutil
        process = psutil.Process()
        return process.memory_info().rss / (1024 * 1024)
    except ImportError:
        return 0.0


def benchmark_battery(steps: int = 100000, dt_s: float = 1.0) -> BenchmarkResult:
    """Benchmark battery simulation performance.
    
    Args:
        steps: Number of simulation steps
        dt_s: Time step in seconds
    
    Returns:
        BenchmarkResult with performance metrics
    """
    config = BatteryConfig(capacity_wh=100.0, initial_soc=0.5)
    state = BatteryState(storage_wh=50.0, capacity_wh=100.0, soc=0.5)
    
    start_time = time.perf_counter()
    for _ in range(steps):
        state = step_battery(state, config, net_power_w=10.0, dt_s=dt_s)
    end_time = time.perf_counter()
    
    duration = end_time - start_time
    memory = _get_memory_usage()
    
    return BenchmarkResult(
        name="battery",
        steps=steps,
        duration_s=duration,
        steps_per_second=steps / duration,
        avg_time_per_step_us=(duration / steps) * 1_000_000,
        memory_mb=memory,
    )


def benchmark_thermal_node(steps: int = 100000, dt_s: float = 1.0) -> BenchmarkResult:
    """Benchmark thermal node simulation performance.
    
    Args:
        steps: Number of simulation steps
        dt_s: Time step in seconds
    
    Returns:
        BenchmarkResult with performance metrics
    """
    config = ThermalNodeConfig(ambient_k=300.0, tau_s=100.0)
    state = ThermalNodeState(temp_k=300.0)
    
    start_time = time.perf_counter()
    for _ in range(steps):
        state = step_thermal_node(state, config, power_w=10.0, dt_s=dt_s)
    end_time = time.perf_counter()
    
    duration = end_time - start_time
    memory = _get_memory_usage()
    
    return BenchmarkResult(
        name="thermal_node",
        steps=steps,
        duration_s=duration,
        steps_per_second=steps / duration,
        avg_time_per_step_us=(duration / steps) * 1_000_000,
        memory_mb=memory,
    )


def benchmark_solar_tracking(steps: int = 100000, dt_s: float = 1.0) -> BenchmarkResult:
    """Benchmark solar tracking simulation performance.
    
    Args:
        steps: Number of simulation steps
        dt_s: Time step in seconds
    
    Returns:
        BenchmarkResult with performance metrics
    """
    config = SolarPanelConfig(max_power_w=120.0)
    state = SolarPanelState(normal_b=(1.0, 0.0, 0.0))
    sun = (1.0, 0.0, 0.0)
    
    start_time = time.perf_counter()
    for _ in range(steps):
        state, power = step_solar_tracking(state, config, sun, shadow=1.0, dt_s=dt_s)
    end_time = time.perf_counter()
    
    duration = end_time - start_time
    memory = _get_memory_usage()
    
    return BenchmarkResult(
        name="solar_tracking",
        steps=steps,
        duration_s=duration,
        steps_per_second=steps / duration,
        avg_time_per_step_us=(duration / steps) * 1_000_000,
        memory_mb=memory,
    )


def benchmark_combined(steps: int = 100000, dt_s: float = 1.0) -> BenchmarkResult:
    """Benchmark combined battery + thermal + solar simulation.
    
    Args:
        steps: Number of simulation steps
        dt_s: Time step in seconds
    
    Returns:
        BenchmarkResult with performance metrics
    """
    battery_config = BatteryConfig(capacity_wh=100.0, initial_soc=0.5)
    battery_state = BatteryState(storage_wh=50.0, capacity_wh=100.0, soc=0.5)
    
    thermal_config = ThermalNodeConfig(ambient_k=300.0, tau_s=100.0)
    thermal_state = ThermalNodeState(temp_k=300.0)
    
    solar_config = SolarPanelConfig(max_power_w=120.0)
    solar_state = SolarPanelState(normal_b=(1.0, 0.0, 0.0))
    sun = (1.0, 0.0, 0.0)
    
    start_time = time.perf_counter()
    for _ in range(steps):
        solar_state, solar_power = step_solar_tracking(solar_state, solar_config, sun, shadow=1.0, dt_s=dt_s)
        battery_state = step_battery(battery_state, battery_config, net_power_w=solar_power - 20.0, dt_s=dt_s)
        thermal_state = step_thermal_node(thermal_state, thermal_config, power_w=20.0, dt_s=dt_s)
    end_time = time.perf_counter()
    
    duration = end_time - start_time
    memory = _get_memory_usage()
    
    return BenchmarkResult(
        name="combined",
        steps=steps,
        duration_s=duration,
        steps_per_second=steps / duration,
        avg_time_per_step_us=(duration / steps) * 1_000_000,
        memory_mb=memory,
    )


def run_all_benchmarks(
    steps: int = 100000,
    dt_s: float = 1.0,
    verbose: bool = True,
) -> List[BenchmarkResult]:
    """Run all benchmarks.
    
    Args:
        steps: Number of simulation steps per benchmark
        dt_s: Time step in seconds
        verbose: Whether to print results
    
    Returns:
        List of BenchmarkResult objects
    """
    benchmarks = [
        benchmark_battery(steps, dt_s),
        benchmark_thermal_node(steps, dt_s),
        benchmark_solar_tracking(steps, dt_s),
        benchmark_combined(steps, dt_s),
    ]
    
    if verbose:
        print("=" * 70)
        print("          sat_sim Performance Benchmark Results")
        print("=" * 70)
        print(f"Steps per benchmark: {steps:,}")
        print(f"Time step: {dt_s}s")
        print("-" * 70)
        print(f"{'Component':<20} {'Steps/s':>12} {'Avg Time':>12} {'Memory':>10}")
        print(f"{'':<20} {'(k)':>12} {'(us/step)':>12} {'(MB)':>10}")
        print("-" * 70)
        
        for result in benchmarks:
            print(f"{result.name:<20} "
                  f"{result.steps_per_second / 1000:>11.2f}k "
                  f"{result.avg_time_per_step_us:>11.2f} "
                  f"{result.memory_mb:>9.1f}")
        
        print("-" * 70)
        
        combined_result = benchmarks[-1]
        real_time_factor = combined_result.steps_per_second * dt_s
        print(f"Real-time factor (combined): {real_time_factor:.1f}x")
        print(f"This means the simulation runs {real_time_factor:.1f}x faster than real time")
        print("=" * 70)
    
    return benchmarks


def compare_configurations(
    configurations: List[Dict[str, Any]],
    benchmark_fn: Callable[..., BenchmarkResult],
    steps: int = 100000,
) -> List[BenchmarkResult]:
    """Compare performance across different configurations.
    
    Args:
        configurations: List of configuration dictionaries
        benchmark_fn: Benchmark function to run
        steps: Number of simulation steps
    
    Returns:
        List of BenchmarkResult objects
    """
    results = []
    for config in configurations:
        print(f"Benchmarking: {config.get('name', 'unknown')}")
        result = benchmark_fn(steps=steps, **{k: v for k, v in config.items() if k != 'name'})
        result = BenchmarkResult(
            name=config.get('name', 'unknown'),
            steps=result.steps,
            duration_s=result.duration_s,
            steps_per_second=result.steps_per_second,
            avg_time_per_step_us=result.avg_time_per_step_us,
            memory_mb=result.memory_mb,
        )
        results.append(result)
        print(f"  Steps/s: {result.steps_per_second / 1000:.2f}k")
    return results


def run_benchmark_suite() -> Dict[str, Any]:
    """Run comprehensive benchmark suite.
    
    Returns:
        Dictionary with all benchmark results and summary
    """
    results = {
        "basic": run_all_benchmarks(steps=100000, verbose=False),
        "fast": run_all_benchmarks(steps=1000000, verbose=False),
    }
    
    summary = {
        "benchmarks": [],
        "fastest_component": None,
        "slowest_component": None,
        "overall_real_time_factor": None,
    }
    
    for mode, mode_results in results.items():
        for result in mode_results:
            summary["benchmarks"].append({
                "mode": mode,
                "component": result.name,
                "steps": result.steps,
                "steps_per_second": result.steps_per_second,
                "avg_time_us": result.avg_time_per_step_us,
            })
    
    basic_results = results["basic"]
    summary["fastest_component"] = max(basic_results, key=lambda r: r.steps_per_second).name
    summary["slowest_component"] = min(basic_results, key=lambda r: r.steps_per_second).name
    
    combined = [r for r in basic_results if r.name == "combined"][0]
    summary["overall_real_time_factor"] = combined.steps_per_second * 1.0
    
    return summary


if __name__ == "__main__":
    run_all_benchmarks()
