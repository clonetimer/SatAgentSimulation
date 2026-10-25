"""Finite native whole config may be re-used in realtime only after review."""
from __future__ import annotations

from copy import deepcopy

import pytest

from sat_sim.form_schema import capability_form_schema
from sat_sim.interactive.offline_config_bridge import preview_finite_config_for_live


def whole():
    return deepcopy(capability_form_schema("whole_spacecraft.unified_native.v1")["default_form"])


def test_changed_orbit_eps_and_rw_values_are_reviewed_and_validated():
    form = whole()
    form["parameters"]["values"]["orbit_radius_m"] = 7_250_000.
    form["parameters"]["values"]["battery_capacity_wh"] = 210.
    form["parameters"]["values"]["rw_configuration"] = "pyramid_4"
    review = preview_finite_config_for_live(form)
    assert review["eligible"], review
    assert review["native_values"]["orbit_radius_m"] == 7_250_000.
    assert review["native_values"]["battery_capacity_wh"] == 210.
    assert review["native_values"]["rw_configuration"] == "pyramid_4"
    assert review["grouped_values"]["orbit"]["orbit_radius_m"] == 7_250_000.
    assert review["grouped_values"]["eps"]["battery_capacity_wh"] == 210.
    assert review["grouped_values"]["adcs"]["rw_configuration"] == "pyramid_4"
    assert "不会导入" in "".join(review["notes"])
    assert review["cross_mode_default_differences"]


def test_unchanged_defaults_do_not_get_silently_mapped_as_identical():
    report = preview_finite_config_for_live(whole())
    assert report["eligible"] is False
    assert report["native_values"] == {}
    assert not report["blocked"]


@pytest.mark.parametrize("event_type", ("faults", "degradations", "constraints"))
def test_offline_events_block_instead_of_being_discarded(event_type):
    form = whole()
    form["parameters"]["values"]["orbit_radius_m"] = 7_250_000.
    form["events"][event_type] = [{"effect": "unsupported_event"}]
    result = preview_finite_config_for_live(form)
    assert not result["eligible"] and result["native_values"] == {}
    assert any("events." + event_type == item["path"] for item in result["blocked"])


def test_unknown_changed_parameter_and_proxy_authorization_block():
    form = whole()
    form["parameters"]["values"]["unknown_satellite_mass"] = 140.
    form["assurance"]["allow_proxy"] = True
    preview = preview_finite_config_for_live(form)
    assert not preview["eligible"]
    assert any("unknown_satellite_mass" in b["path"] for b in preview["blocked"])
    assert any(b["path"] == "assurance.allow_proxy" for b in preview["blocked"])


def test_rejects_component_and_conflicting_capability_id():
    form = whole()
    form["capability_id"] = "component.battery.v1"
    with pytest.raises(ValueError, match="CAPABILITY_CONFLICT"):
        preview_finite_config_for_live(form)
    form["model"]["capability_id"] = "component.battery.v1"
    with pytest.raises(ValueError, match="REQUIRES_OFFLINE_NATIVE_WHOLE"):
        preview_finite_config_for_live(form)


def test_api_preview_has_no_run_side_effect_and_rejects_incompatible(tmp_path):
    from fastapi.testclient import TestClient
    from sat_sim.api import create_app

    run_root = tmp_path / "runs"
    app = create_app(runs_root=run_root, artifacts_root=tmp_path / "api",
                     embedded_worker=False, auth_mode="disabled")
    form = whole()
    form["parameters"]["values"]["battery_capacity_wh"] = 215.
    with TestClient(app) as client:
        response = client.post("/interactive/continuous/configuration/preview-from-finite",
                               json={"form_data": form})
        assert response.status_code == 200, response.text
        assert response.json()["eligible"] is True
        assert response.json()["native_values"]["battery_capacity_wh"] == 215.
        form["events"]["faults"] = [{"effect": "rw_fault"}]
        blocked = client.post("/interactive/continuous/configuration/preview-from-finite",
                              json={"form_data": form})
        assert blocked.status_code == 200 and blocked.json()["eligible"] is False
        assert not blocked.json()["native_values"]
        invalid = client.post("/interactive/continuous/configuration/preview-from-finite",
                              json={"form_data": {"capability_id": "component.battery.v1"}})
        assert invalid.status_code == 422
    assert not list(run_root.iterdir()) if run_root.exists() else True
