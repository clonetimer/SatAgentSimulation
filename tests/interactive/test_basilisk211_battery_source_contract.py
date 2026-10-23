"""R9-J C++ source-patch ABI acceptance barriers (not physical parity)."""
from __future__ import annotations

import pytest

from sat_sim.interactive.native_battery_checkpoint_compat import (
    inspect_battery_native_checkpoint_abi,
    require_battery_native_checkpoint_abi,
)


def test_r9j_native_cpp_battery_patch_cannot_fake_whole_restart():
    status = inspect_battery_native_checkpoint_abi()
    assert status["evidence_level"] in (
        "SOURCE_ONLY_NOT_COMPILED",
        "SOURCE_ONLY_UNPATCHED_WHEEL",
        "ABI_DETECTED_NOT_PARITY_PROVEN",
    )
    assert status["whole_spacecraft_resume_supported"] is False
    assert type(status["native_cpp_patch_abi_available"]) is bool
    if not status["native_cpp_patch_abi_available"]:
        with pytest.raises(RuntimeError, match=(
            "R9J_NATIVE_CPP_REBUILD_REQUIRED|R9J_BASILISK_NOT_INSTALLED"
        )):
            require_battery_native_checkpoint_abi()
    else:
        # ABI presence alone is never proof of native time/message parity.
        assert status["evidence_level"] == "ABI_DETECTED_NOT_PARITY_PROVEN"
        require_battery_native_checkpoint_abi()


def test_r9j_upstream_source_patcher_pins_exact_commit_and_never_auto_builds():
    from pathlib import Path
    import importlib.util
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts" / "apply_basilisk211_battery_checkpoint.py"
    )
    assert script.is_file()
    spec = importlib.util.spec_from_file_location("r9j_native_patch_script", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.UPSTREAM_COMMIT == "6c1511f5385dccaa73c9ae24522a74adc24fe623"
    assert module.PATCH_MARKER in module.CHECKPOINT_DEFINITION
    assert "storedCharge" in module.CPP_METHODS
    assert "previousTime" in module.CPP_METHODS
    assert "Reset()" in module.CPP_METHODS
    assert "this->writeMessages(targetTimeNs)" in module.CPP_METHODS
    assert "std::invalid_argument" in module.CPP_METHODS
