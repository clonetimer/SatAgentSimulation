"""Extract compact DT evidence from the real-browser test reports and archives."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def read(path):
    return json.loads(path.read_bytes())


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def model_evidence(path):
    generation = read(path)
    result = generation["response"]["result"]
    attempt = Path(result["files"]["first_attempt"])
    first = read(attempt)
    calls = []
    for call in sorted(attempt.parent.glob("model_call*.json")):
        item = read(call)
        invocation = item.get("invocation_evidence") or {}
        usage = invocation.get("usage") or {}
        total = usage.get("total_tokens")
        source = "provider"
        if total is None:
            parts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens")]
            valid = all(type(n) is int and n >= 0 for n in parts)
            total, source = (sum(parts), "derived") if valid else (None, "unknown")
        calls.append({"file": call.name, "file_sha256": digest(call),
                      "id": invocation.get("request_id"), "model": invocation.get("model_id"),
                      "usage": usage, "total_tokens": total, "total_source": source,
                      "latency_ms": invocation.get("latency_ms"),
                      "response_sha256": invocation.get("response_sha256")})
    assert calls and result["facade_result"]["backend_identity"]["actual_model_execution_verified"]
    return {"prompt": generation["prompt"], "elapsed_s": generation["elapsed_s"], "calls": calls,
            **{key: first[key] for key in ("first_answer_pass", "pre_validation_corrections", "model_fallback_used")}}


def check_trace(path, live):
    rows = [json.loads(line) for line in path.read_bytes().splitlines()]
    if live:
        rows = [r["frame"] for r in rows if r["frame"]["stream"] == "runtime.trace"]
    time_key = "sim_time_s" if live else "time_s"
    times = [row[time_key] for row in rows]
    assert times[0] == 0 and len(rows) > 1
    errors = [abs(t - i * 0.5) for i, t in enumerate(times)]
    assert all(error <= math.ulp(i * 0.5) for i, error in enumerate(errors))
    first = rows[0]["values"] if live else rows[0]
    rates = [first[f"attitude.truth.omega_BN_B_{axis}"] for axis in "xyz"]
    assert all(abs(a - b) <= 1e-14 for a, b in zip(rates, [-0.003, 0.0, 0.006]))
    return {"native_frames": len(rows), "first_last_s": [times[0], times[-1]],
            "sample_s": 0.5, "max_time_grid_float_error_s": max(errors),
            "first_body_rates_rad_s": rates, "telemetry_sha256": digest(path)}


def collect(reports, workspace, commit, output):
    paths = list(workspace.rglob("telemetry.jsonl"))
    for entry in ("form", "json", "graph", "scene", "natural_language"):
        for mode in ("offline", "bounded", "continuous"):
            live = mode != "offline"
            if live:
                prefix = "scene_custom" if entry == "scene" else entry
                report_path = reports / "live" / f"{prefix}-{mode}" / "report.json"
            else:
                folder = "custom" if entry == "form" else entry
                report_path = reports / "offline" / folder / "offline-ui-native-report.json"
            r = read(report_path)
            assert r["status"] == "PASS", report_path
            case = {"entry": entry, "mode": mode, "candidate_commit": commit,
                    "report_sha256": digest(report_path), "basilisk": r["basilisk_version"]}
            if live:
                matches = [p for p in paths if p.parent.name == r["session_id"] and
                           p.parents[1].name == "interactive_sessions" and digest(p) == r["telemetry_sha256"]]
                assert len(matches) == 1, (report_path, matches)
                trace = matches[0]
                case.update({k: r[k] for k in ("session_id", "creation_payload", "runtime_capabilities",
                                                "native_epoch_utc", "completion", "selected_object",
                                                "pause", "step", "resume", "safe_mode_command",
                                                "reconnected_session_id") if k in r})
                assert r["completion"]["archive_status"] == "SEALED"
                spec_path = trace.parent / "session_spec.json"
                spec = read(spec_path)["session_spec"]["task_spec"]
                validation_path = trace.parent / "validation_outcome.json"
            else:
                trace, = [p for p in paths if p.parent.name == "results" and p.parents[1].name == r["run_id"]]
                case.update({k: r[k] for k in ("run_id", "run_status", "integrity", "native_epoch_utc",
                                                "scene_quality", "scene_frame_source", "selected_object_label") if k in r})
                assert r["sealed"] and r["integrity"] == "PASS"
                spec_path = trace.parents[1] / "input/task_spec.json"
                spec = read(spec_path)
                validation_path = trace.parents[1] / "validation/validation_outcome.json"
            case["sealed_spec_sha256"] = digest(spec_path)
            sim = spec["simulation"]
            step = sim.get("step_s", sim.get("solver", {}).get("step_s"))
            assert step == 0.1 and sim["sample_s"] == 0.5
            values = spec["parameters"]["values"]
            case["simulation_seed"] = {"value": values.get("simulation_seed", 0),
                                       "source": "explicit" if "simulation_seed" in values else "native default"}
            assert case["simulation_seed"]["value"] == 0 and sim.get("random_seed", 0) == 0
            validation = read(validation_path)
            assert validation["execution_status"] == "SUCCEEDED" and validation["result"] == "PASS"
            case["validation"] = {k: validation[k] for k in ("result", "execution_status", "reason_code")}
            case["validation_sha256"] = digest(validation_path)
            case.update(check_trace(trace, live))
            if entry == "natural_language":
                case["generation"] = model_evidence(report_path.parent / "generation.json")
            (output / f"{entry}_{mode}.json").write_text(json.dumps(case, indent=2, ensure_ascii=False) + "\n", encoding="utf8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", type=Path)
    parser.add_argument("pytest_workspace", type=Path)
    parser.add_argument("candidate_commit")
    args = parser.parse_args()
    collect(args.reports, args.pytest_workspace, args.candidate_commit, Path(__file__).resolve().parent)
