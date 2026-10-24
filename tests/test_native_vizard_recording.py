"""Vizard native recording smoke, no Unity impersonation or satellite proxies."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from sat_sim.bsk_engine.vizard_native_export import (
    validate_local_vizard_destination, verify_native_vizard_recording,
)


def test_destination_rejects_non_bin_existing_and_symlink(tmp_path):
    with pytest.raises(ValueError, match="VIZARD_OUTPUT_MUST_BE_BIN"):
        validate_local_vizard_destination(tmp_path / "test.csv")
    path = tmp_path / "good.bin"
    assert validate_local_vizard_destination(path) == path.resolve()
    path.write_bytes(b"existing")
    with pytest.raises(FileExistsError, match="VIZARD_OUTPUT_ALREADY_EXISTS"):
        validate_local_vizard_destination(path)
    link = tmp_path / "fake.bin"
    try:
        link.symlink_to(path)
    except OSError:
        return
    with pytest.raises(ValueError, match="VIZARD_OUTPUT_SYMLINK_FORBIDDEN"):
        validate_local_vizard_destination(link)


def test_short_vizard_file_rejected_as_native_evidence(tmp_path):
    path = tmp_path / "empty.bin"
    path.write_bytes(b"fake")
    with pytest.raises(RuntimeError, match="VIZARD_NATIVE_RECORDING_EMPTY"):
        verify_native_vizard_recording(path)


def test_vizard_recorder_is_opt_in_and_finite_only(tmp_path):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import vizSupport
    assert vizSupport.vizFound, "bsk wheel does not expose native vizInterface"
    from sat_sim.bsk_engine.unified_native import (
        UnifiedNativeRuntime, _config, WHOLE_UNIFIED_CAPABILITY_ID,
    )
    from test_unified_native_migration import _short_spec
    from dataclasses import replace
    spec = _short_spec(WHOLE_UNIFIED_CAPABILITY_ID, duration_s=3.)
    cfg = _config(spec, WHOLE_UNIFIED_CAPABILITY_ID, False)
    path = tmp_path / "native.bin"
    assert cfg.vizard_save_file is None
    with pytest.raises(ValueError, match="VIZARD_NATIVE_RECORDING_FINITE_ONLY"):
        UnifiedNativeRuntime(replace(
            cfg, vizard_save_file=str(path), interactive_unbounded=True,
        )).run()


def test_real_upstream_basilisk_writes_vizard_bin_without_changing_physics(tmp_path):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import vizSupport
    assert vizSupport.vizFound, "bsk wheel does not expose native vizInterface"
    from sat_sim.bsk_engine.unified_native import (
        UnifiedNativeRuntime, _config, WHOLE_UNIFIED_CAPABILITY_ID,
    )
    from dataclasses import replace
    from test_unified_native_migration import _short_spec

    spec = _short_spec(WHOLE_UNIFIED_CAPABILITY_ID, duration_s=6., values={
        "initial_pointing_error_deg": 9.,
    })
    cfg = _config(spec, WHOLE_UNIFIED_CAPABILITY_ID, False)
    reference = UnifiedNativeRuntime(cfg).run()
    path = tmp_path / "real_native.bin"
    recorded = UnifiedNativeRuntime(replace(cfg, vizard_save_file=str(path))).run()
    evidence = recorded.metadata["vizard_native_recording"]
    assert path.is_file()
    assert path.stat().st_size > 64
    assert evidence["status"] == "RECORDED"
    assert evidence["sha256"] and len(evidence["sha256"]) == 64
    assert evidence["format"] == "basilisk_vizInterface_binary"
    assert evidence["embedded_browser_vizard_available"] is False
    assert recorded.summary["vizard_native_recording_status"] == "RECORDED"
    assert "native_vizard_vizInterface" in recorded.metadata["model_source_boundary"]["basilisk_official_native"]
    assert recorded.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert [r["time_s"] for r in reference.trace_rows] == [r["time_s"] for r in recorded.trace_rows]
    for key in ("adcs.pointing_error_deg", "orbit.radius_m", "adcs.rw.speed_rad_s_0"):
        assert [r[key] for r in reference.trace_rows] == pytest.approx(
            [r[key] for r in recorded.trace_rows], abs=1e-8, rel=1e-10
        )


def test_local_cli_writes_manifest_with_real_native_bin(tmp_path):
    pytest.importorskip("Basilisk")
    from Basilisk.utilities import vizSupport
    assert vizSupport.vizFound
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "scripts/export_native_vizard.py"
    module_spec = importlib.util.spec_from_file_location("export_native_vizard", script)
    module = importlib.util.module_from_spec(module_spec)
    assert module_spec.loader
    module_spec.loader.exec_module(module)
    data = module.create_recording(output=tmp_path / "demo.bin", duration_s=3.)
    assert data["status"] == "RECORDED"
    assert data["native_runtime_truth"] == "instantiated_connected_recorded_unified"
    manifest = json.loads((tmp_path / "demo.vizard.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == data["sha256"]
