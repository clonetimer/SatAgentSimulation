from __future__ import annotations

from importlib.resources import files
import json
from pathlib import Path

import pytest

from sat_sim.interactive.command_catalog import CommandCatalog, CommandValidationError
from sat_sim.interactive.models import Telecommand


def test_packaged_default_matrix_matches_source_contract_bytes() -> None:
    resource = files("sat_sim.interactive").joinpath("realtime_capability_matrix.json")
    source = Path(__file__).resolve().parents[2] / "configs/interactive/realtime_capability_matrix.json"
    assert resource.read_bytes() == source.read_bytes()


def test_explicit_matrix_path_remains_supported(tmp_path: Path) -> None:
    payload = json.loads(files("sat_sim.interactive").joinpath("realtime_capability_matrix.json").read_text(encoding="utf-8"))
    payload["initial_release_capabilities"][0]["commands"][0]["operation"] = "session.custom.set"
    path = tmp_path / "custom-matrix.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert "session.custom.set" in CommandCatalog.from_matrix(path).operations()
    assert "session.mode.set" not in CommandCatalog.from_matrix(path).operations()
    assert "session.mode.set" in CommandCatalog.default().operations()
    assert "session.custom.set" not in CommandCatalog.default().operations()


def test_missing_default_packaged_matrix_fails_closed(tmp_path: Path, monkeypatch) -> None:
    import sat_sim.interactive.command_catalog as module

    monkeypatch.setattr(module, "files", lambda package: tmp_path)
    with pytest.raises(FileNotFoundError, match="realtime_capability_matrix.json"):
        CommandCatalog.default()


def _command(operation: str, target: str, parameters: dict, role: str = "operator") -> Telecommand:
    return Telecommand(
        command_id="catalog-command", session_id="session-1", session_revision=2,
        operation=operation, target=target, parameters=parameters,
        actor_id="actor-1", actor_role=role, execute_at_sim_time_s=0.5,
    )


def test_catalog_loads_all_frozen_initial_operations() -> None:
    catalog = CommandCatalog.default()
    assert len(catalog.operations()) == 10
    assert {"session.mode.set", "adcs.target.set", "eps.load.set", "comm_data.downlink.set"} <= set(catalog.operations())


def test_catalog_validates_target_parameters_and_role_fail_closed() -> None:
    catalog = CommandCatalog.default()
    contract = catalog.validate(_command("adcs.target.set", "subsystem.adcs", {"sigma_rn": [0.1, 0.0, 0.0]}))
    assert contract.effect_fields == ("adcs.pointing_error_deg", "adcs.sigma_br_norm")
    cases = [
        (_command("unknown.operation", "subsystem.adcs", {}), "UNKNOWN_OPERATION"),
        (_command("adcs.target.set", "subsystem.eps", {"sigma_rn": [0.1, 0.0, 0.0]}), "TARGET_MISMATCH"),
        (_command("adcs.target.set", "subsystem.adcs", {"sigma_rn": [2.0, 0.0, 0.0]}), "PARAMETER_VALIDATION_FAILED"),
        (_command("adcs.target.set", "subsystem.adcs", {"sigma_rn": [0.1, 0.0, 0.0], "code": "x"}), "PARAMETER_VALIDATION_FAILED"),
        (_command("adcs.fault.inject", "subsystem.adcs", {"fault_id": "rw_jam", "severity": 1.0}), "AUTHORIZATION_DENIED"),
    ]
    for command, reason in cases:
        with pytest.raises(CommandValidationError) as captured:
            catalog.validate(command)
        assert captured.value.reason_code == reason


def test_fault_operator_and_admin_can_use_registered_fault_contract() -> None:
    catalog = CommandCatalog.default()
    for role in ("fault_operator", "admin"):
        command = _command(
            "adcs.fault.inject", "subsystem.adcs",
            {"fault_id": "rw_jam", "severity": 0.5}, role,
        )
        assert catalog.validate(command).required_role.value == "fault_operator"
