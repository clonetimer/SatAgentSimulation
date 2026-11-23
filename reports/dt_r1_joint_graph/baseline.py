"""Compare the historical engineering trace at the unchanged six-second initial-rate baseline."""
import csv
import gzip
import json
import sys
from pathlib import Path
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.capability_registry import get_adapter_for_capability
from sat_sim.task_compiler import compile_task_spec
from sat_sim.unified_execution import build_execution_request

spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="initial_rate_test")
spec["simulation"].update(duration_s=6., step_s=.1, sample_s=.3)
request = build_execution_request(compile_task_spec(spec), spec)
rows = get_adapter_for_capability(request.capability_id).run(request.metadata["resolved_runtime_task_spec"]).trace_rows
fields = ["time_s", *[f"attitude.truth.omega_BN_B_{a}" for a in "xyz"], "thermal.battery_temp_c"]
with gzip.open(Path("reports/external_reference_alignment/model_telemetry.csv.gz"), "rt") as f:
    reader = csv.DictReader(f)
    deltas = [{k:r[k]-float(old[k]) for k in fields} for r,old in zip(rows,reader)]
assert len(deltas) == 21
print(json.dumps({k:max(abs(d[k]) for d in deltas) for k in fields}))
