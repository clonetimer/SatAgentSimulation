from __future__ import annotations

import json
from pathlib import Path

import pytest

from sat_sim.capacity_benchmark import (
    CAPACITY_SCHEMA_VERSION,
    execute_worker_request,
    run_capacity_plan,
    summarize_samples,
)


def _thermal_template(tmp_path: Path) -> Path:
    path = tmp_path / "thermal.yaml"
    path.write_text(
        """schema_version: '1.0.0'
task: {id: capacity_thermal, name: capacity thermal}
simulation: {level: subsystem, subsystem: thermal, duration_s: 10.0, step_s: 1.0, sample_s: 1.0, backend: python}
parameters:
  profile: demo
  values: {initial_battery_temp_k: 290.0, payload_power_w: 20.0}
outputs:
  output_root: datasets/capacity_thermal
  qoi: [thermal.source_native.battery_temp_k]
assurance: {parameter_profile: demo, allow_proxy: false}
model:
  capability_id: subsystem.thermal.source_native.v1
  target: {level: subsystem, name: thermal, mode: nominal}
""",
        encoding="utf-8",
    )
    return path


def test_summarize_samples_reports_interpolated_percentiles() -> None:
    summary = summarize_samples([1, 2, 3, 4])
    assert summary == {
        "count": 4,
        "min": 1.0,
        "max": 4.0,
        "mean": 2.5,
        "p50": 2.5,
        "p95": pytest.approx(3.85),
    }


def test_worker_records_non_legacy_metrics(tmp_path: Path) -> None:
    template = _thermal_template(tmp_path)
    payload = execute_worker_request(
        {
            "schema_version": CAPACITY_SCHEMA_VERSION,
            "workload_id": "thermal-test",
            "run_id": "thermal-test-01",
            "template_path": str(template),
            "duration_s": 20.0,
            "sample_s": 2.0,
            "output_root": str(tmp_path / "dataset"),
            "write_dataset": True,
        }
    )
    assert payload["status"] == "PASS"
    assert payload["adapter_key"] == "python.thermal_source_native_graph"
    assert payload["legacy_mode"] is False
    assert payload["legacy_bridge_called"] is False
    assert payload["trace_rows"] > 0
    assert payload["output_bytes"] > 0
    assert payload["wall_time_s"] > 0
    assert payload["real_time_factor"] > 0
    assert payload["model_graph_sha256"]


def test_worker_expands_governed_event_density(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    payload = execute_worker_request(
        {
            "schema_version": CAPACITY_SCHEMA_VERSION,
            "workload_id": "battery-fault-density-test",
            "run_id": "battery-fault-density-test-01",
            "template_path": str(
                root / "examples" / "component_battery_capability_fault.yaml"
            ),
            "duration_s": 120.0,
            "sample_s": 1.0,
            "event_repetitions": 8,
            "output_root": str(tmp_path / "dataset"),
            "write_dataset": True,
        }
    )
    assert payload["status"] == "PASS"
    assert payload["event_count"] == 8
    assert payload["trace_rows"] == 121


def test_controller_generates_formal_reports(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    template = _thermal_template(tmp_path)
    # Put the plan under a two-level config path because the production plan uses
    # plan.parents[2] as project root for resolving relative templates.
    config_dir = root / ".capacity_test" / "configs"
    config_dir.mkdir(parents=True, exist_ok=True)
    plan_path = config_dir / "plan.json"
    relative_template = template.relative_to(root) if template.is_relative_to(root) else None
    if relative_template is None:
        # The controller requires project-relative templates. Copy the fixture to
        # a temporary project-owned location and remove it in the test cleanup.
        fixture_dir = root / ".capacity_test" / "fixtures"
        fixture_dir.mkdir(parents=True, exist_ok=True)
        project_template = fixture_dir / "thermal.yaml"
        project_template.write_bytes(template.read_bytes())
        relative_template = project_template.relative_to(root)
    plan = {
        "schema_version": CAPACITY_SCHEMA_VERSION,
        "benchmark_id": "test-capacity-v1",
        "worker_timeout_s": 60,
        "guardrails": {"minimum_success_rate": 1.0, "require_non_legacy": True},
        "workloads": [
            {
                "workload_id": "thermal-controller-test",
                "dimension": "test",
                "template": relative_template.as_posix(),
                "duration_s": 10,
                "sample_s": 1,
                "concurrency": 1,
                "waves": 1,
            }
        ],
        "not_validated": ["test-only"],
    }
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    output = tmp_path / "report"
    try:
        report = run_capacity_plan(
            plan_path=plan_path,
            output_dir=output,
            script_path=root / "scripts" / "run_capacity_benchmark.py",
        )
    finally:
        import shutil

        shutil.rmtree(root / ".capacity_test", ignore_errors=True)
    assert report["status"] == "PASS"
    assert report["summary"] == {"job_count": 1, "pass_count": 1, "fail_count": 0, "success_rate": 1.0}
    assert (output / "capacity_report.json").is_file()
    assert (output / "capacity_report.md").is_file()
    assert (output / "capacity_results.csv").is_file()
