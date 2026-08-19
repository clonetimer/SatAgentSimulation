from __future__ import annotations

import json
from pathlib import Path

import tomllib

import sat_sim
from sat_sim.adapters.subsystem_adcs_bsksim import adcs_config_from_task_spec
from sat_sim.bsk_engine.scenario_factory import config_from_task_spec
from sat_sim.bsk_engine.whole_spacecraft_coupled import whole_spacecraft_coupled_config_from_task_spec
from sat_sim.capability_registry import active_capability_ids, list_capabilities
from sat_sim.release_closure import RELEASE_ID, RELEASE_VERSION, release_manifest
from sat_sim.scenario_templates import list_scenario_templates
from sat_sim.task_models import canonicalize_task_spec, to_runtime_task_spec


ROOT = Path(__file__).resolve().parents[1]
EXPECTED_VERSION = "0.7.8"
EXPECTED_RELEASE_ID = "SAT-SIM-0.7.8-ENGINEERING-BASELINE"


def test_v0546_release_identity_is_consistent() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    manifest = release_manifest()
    html = (ROOT / "src/sat_sim/web/index.html").read_text(encoding="utf-8")

    assert sat_sim.__version__ == EXPECTED_VERSION
    assert pyproject["project"]["version"] == EXPECTED_VERSION
    assert RELEASE_VERSION == EXPECTED_VERSION
    assert RELEASE_ID == EXPECTED_RELEASE_ID
    assert manifest["release_version"] == EXPECTED_VERSION
    assert manifest["release_id"] == EXPECTED_RELEASE_ID
    assert f"styles.css?v={EXPECTED_VERSION}" in html
    assert f'app.js?v={EXPECTED_VERSION}' in html
    assert f'window.SAT_SIM_STATIC_VERSION = "{EXPECTED_VERSION}"' in html


def test_v0546_release_manifest_counts_match_runtime_catalogs() -> None:
    manifest = release_manifest()
    templates = list_scenario_templates()
    assert manifest["capability_contract_count"] == len(list_capabilities())
    assert manifest["active_agent_capability_count"] == len(active_capability_ids())
    assert manifest["scenario_template_count"] == templates["count"]
    assert templates["count"] == len(templates["templates"])


def test_v0546_bsksim_adapters_preserve_canonical_solver_step() -> None:
    base = {
        "schema_version": "1.0.0",
        "task": {"id": "step_contract", "name": "step contract", "description": "", "tags": []},
        "simulation": {
            "level": "whole_spacecraft",
            "duration_s": 60.0,
            "sample_s": 5.0,
            "step_s": 0.5,
            "random_seed": 0,
            "backend": "selective_unified_basilisk_assembly",
            "time_base": "simulation_seconds",
            "time_system": "UTC",
        },
        "parameters": {"profile": "demo", "values": {}, "overrides": []},
        "events": {"faults": [], "degradations": [], "constraints": []},
        "outputs": {
            "output_root": "runs/step_contract",
            "trace_format": "csv",
            "qoi": ["status"],
            "files": [],
            "plots": [],
            "include_summary": True,
            "include_trace": True,
            "include_labels": True,
            "include_manifest": True,
        },
        "assurance": {
            "fidelity_level": "declared_by_capability",
            "claim_level": "analysis_only",
            "validation_profile": "default",
            "parameter_profile": "demo",
            "allow_proxy": False,
        },
        "model": {
            "capability_id": "whole_spacecraft.bsksim_foundation.v1",
            "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "nominal"},
            "spacecraft": {},
            "orbit_environment": {},
            "config": {},
            "legacy_degradations": {},
            "campaign": {},
            "simulation_extra": {},
            "validation": {},
        },
        "provenance": {"fields": {}, "assumptions": [], "pending_confirmations": []},
        "metadata": {},
    }
    canonical = canonicalize_task_spec(base)
    runtime = to_runtime_task_spec(canonical)
    assert runtime["simulation"]["solver"]["step_s"] == 0.5
    assert "step_s" not in {k for k in runtime["simulation"] if k != "solver"}
    assert config_from_task_spec(runtime).step_s == 0.5

    runtime["capability_id"] = "subsystem.adcs_bsksim.v1"
    runtime["task_type"] = "subsystem"
    assert adcs_config_from_task_spec(runtime).step_s == 0.5

    runtime["capability_id"] = "whole_spacecraft.bsksim_coupled.v1"
    runtime["task_type"] = "whole_spacecraft"
    assert whole_spacecraft_coupled_config_from_task_spec(runtime).step_s == 0.5


def test_platform_acceptance_and_runtime_audit_use_current_release_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    upgrade = (root / "scripts" / "upgrade_windows.ps1").read_text(encoding="utf-8")
    acceptance = (root / "scripts" / "run_platform_acceptance.py").read_text(encoding="utf-8")
    package_verify = (root / "scripts" / "verify_source_package.py").read_text(encoding="utf-8")
    audit = (root / "scripts" / "check_workbench_reports_diagnostics.py").read_text(encoding="utf-8")
    assert '[string]$ExpectedVersion = "0.7.8"' in upgrade
    assert 'default="0.7.8"' in acceptance
    assert 'default="SAT-SIM-0.7.8-ENGINEERING-BASELINE"' in acceptance
    assert "sat-sim.platform-acceptance.v1" in acceptance
    assert "strict_release_check" in acceptance
    assert "vllm_acceptance" in acceptance
    assert "RELEASE_ID as EXPECTED_RELEASE_ID" in package_verify
    assert 'EXPECTED_RELEASE_VERSION = "0.5.7.5"' not in package_verify
    assert '$Response.release.release_version' in upgrade
    assert '$Response.release.package_version' not in upgrade
    assert 'from sat_sim.release_closure import RELEASE_ID, RELEASE_VERSION' in audit
    assert '__version__ == RELEASE_VERSION' in audit
    assert '"release_id": RELEASE_ID' in audit
    assert 'SAT-SIM-0.5.3.9' not in audit
