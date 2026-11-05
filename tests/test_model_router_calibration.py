from __future__ import annotations

import json

from sat_sim.model_router import ModelRouteConfig, route_model


def _write_calibration(tmp_path, segment):
    path = tmp_path / "route-calibration.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "sat-sim.model-route-calibration.v1",
                "min_samples": 10,
                "min_success_rate_gain": 0.10,
                "segments": [segment],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def _remote_config(path, *, force_tier=None):
    return ModelRouteConfig(
        local_backend="template",
        remote_backend="openai_compatible",
        remote_model="remote-model",
        remote_base_url="http://example.invalid/v1",
        force_tier=force_tier,
        calibration_file=str(path),
    )


def test_route_calibration_can_upgrade_l1_with_matched_budget_evidence(tmp_path):
    path = _write_calibration(
        tmp_path,
        {
            "segment_id": "eps-evaluated",
            "match": {"domains": ["eps"]},
            "sample_count": 30,
            "l1_success_rate": 0.55,
            "l2_success_rate": 0.80,
            "recommended_tier": "L2",
            "matched_budget": True,
            "evaluation_sha256": "a" * 64,
        },
    )
    decision = route_model(
        input_kind="natural_language",
        request_text="请建立一个电池工程仿真。",
        config=_remote_config(path),
    )
    assert decision.tier == "L2"
    assert decision.backend == "openai_compatible"
    assert decision.calibration["status"] == "accepted"
    assert decision.calibration["segment_id"] == "eps-evaluated"
    assert "route_calibration:eps-evaluated:L2" in decision.signals


def test_route_calibration_does_not_change_tier_without_enough_samples(tmp_path):
    path = _write_calibration(
        tmp_path,
        {
            "segment_id": "eps-small",
            "match": {"domains": ["eps"]},
            "sample_count": 4,
            "l1_success_rate": 0.40,
            "l2_success_rate": 0.90,
            "recommended_tier": "L2",
            "matched_budget": True,
            "evaluation_sha256": "b" * 64,
        },
    )
    decision = route_model(
        input_kind="natural_language",
        request_text="请建立一个电池工程仿真。",
        config=_remote_config(path),
    )
    assert decision.tier == "L1"
    assert decision.backend == "template"
    assert decision.calibration["status"] == "insufficient_evidence"


def test_route_calibration_requires_matched_budget_and_evidence_hash(tmp_path):
    path = _write_calibration(
        tmp_path,
        {
            "segment_id": "eps-untrusted",
            "match": {"domains": ["eps"]},
            "sample_count": 100,
            "l1_success_rate": 0.20,
            "l2_success_rate": 0.95,
            "recommended_tier": "L2",
            "matched_budget": False,
            "evaluation_sha256": "not-a-hash",
        },
    )
    decision = route_model(
        input_kind="natural_language",
        request_text="请建立一个电池工程仿真。",
        config=_remote_config(path),
    )
    assert decision.tier == "L1"
    assert decision.calibration["status"] == "insufficient_evidence"
    assert decision.calibration["matched_budget"] is False
    assert decision.calibration["evidence_valid"] is False


def test_forced_tier_remains_authoritative_over_calibration(tmp_path):
    path = _write_calibration(
        tmp_path,
        {
            "segment_id": "eps-evaluated",
            "match": {"domains": ["eps"]},
            "sample_count": 50,
            "l1_success_rate": 0.50,
            "l2_success_rate": 0.90,
            "recommended_tier": "L2",
            "matched_budget": True,
            "evaluation_sha256": "c" * 64,
        },
    )
    decision = route_model(
        input_kind="natural_language",
        request_text="请建立一个电池工程仿真。",
        config=_remote_config(path, force_tier="L1"),
    )
    assert decision.tier == "L1"
    assert decision.backend == "template"
    assert decision.calibration["status"] == "accepted"


def test_route_calibration_can_downgrade_heuristic_l2_when_evidence_favors_l1(tmp_path):
    path = _write_calibration(
        tmp_path,
        {
            "segment_id": "multi-domain-local-better",
            "match": {"signals": ["multi_domain", "workflow_or_coupling"]},
            "sample_count": 40,
            "l1_success_rate": 0.90,
            "l2_success_rate": 0.65,
            "recommended_tier": "L1",
            "matched_budget": True,
            "evaluation_sha256": "d" * 64,
        },
    )
    decision = route_model(
        input_kind="natural_language",
        request_text="同时对 ADCS 和 EPS 做参数扫描，然后与基线对比。",
        config=_remote_config(path),
    )
    assert decision.complexity_score >= 5
    assert decision.tier == "L1"
    assert decision.backend == "template"
    assert decision.calibration["status"] == "accepted"
    assert decision.calibration["recommended_tier"] == "L1"


def test_invalid_calibration_file_is_observable_but_non_authoritative(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    decision = route_model(
        input_kind="natural_language",
        request_text="请建立一个电池工程仿真。",
        config=_remote_config(path),
    )
    assert decision.tier == "L1"
    assert decision.calibration["status"] == "invalid"
    assert decision.calibration["reason"] == "calibration_file_unreadable"
