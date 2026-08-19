from __future__ import annotations

import json
from pathlib import Path

import pytest

from sat_sim.astrograph_roundtrip import apply_astrograph_closure


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "tests" / "fixtures" / "phase3g_roundtrip"


def _case(role: str) -> Path:
    return SAMPLE / role


def _closure() -> dict:
    return {
        "schema_version": "astrograph-sat-sim-pipeline-closure.v1",
        "closure_id": "SAT-SIM-CLOSURE-TEST",
        "closure_sha256": "a" * 64,
        "fault_id": "ADCS_RW_JAM",
        "formal_diagnosis_publishable": False,
        "training_ready": False,
        "external_stage_outputs": {
            "ml_model": {"status": "CANDIDATE_SCREENING_ONLY"},
            "kg_reasoner": {"status": "CANDIDATE_MAPPING_PROJECTION"},
            "fusion": {"status": "REVIEW_REQUIRED"},
        },
    }


def test_roundtrip_completes_only_allowed_external_stages(tmp_path: Path) -> None:
    # Copying is unnecessary: persist=False keeps the source evidence immutable.
    result = apply_astrograph_closure(
        fault_root=_case("fault"), nominal_root=_case("nominal"), closure=_closure(), persist=False
    )
    assert result["completed_external_stages"] == ["fusion", "kg_reasoner", "ml_model"]
    assert result["pending_expert_gate"] is True
    assert result["formal_diagnosis_publishable"] is False
    assert result["training_ready"] is False


def test_roundtrip_rejects_external_publication_assertion() -> None:
    closure = _closure()
    closure["formal_diagnosis_publishable"] = True
    with pytest.raises(ValueError, match="formal diagnosis"):
        apply_astrograph_closure(
            fault_root=_case("fault"), nominal_root=_case("nominal"), closure=closure, persist=False
        )


def test_roundtrip_rejects_expert_gate_completion() -> None:
    closure = _closure()
    closure["external_stage_outputs"]["expert_gate"] = {"status": "APPROVED"}
    with pytest.raises(ValueError, match="unsupported AstroGraph external stages|expert"):
        apply_astrograph_closure(
            fault_root=_case("fault"), nominal_root=_case("nominal"), closure=closure, persist=False
        )
