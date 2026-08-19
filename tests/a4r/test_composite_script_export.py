from __future__ import annotations

from pathlib import Path

from sat_sim.script_exporter import export_runner_script


def test_composite_capability_python_export_uses_unified_execution(tmp_path: Path) -> None:
    output = tmp_path / "composite_runner.py"

    export_runner_script(
        "examples/whole_spacecraft_composite_digital_twin_nominal.yaml",
        output,
        kind="capability-python",
        output_root=tmp_path / "runs",
    )
    text = output.read_text(encoding="utf-8")

    assert "from sat_sim.unified_execution import execute_compiled_task" in text
    assert "execute_compiled_task(" in text
    assert "ADAPTER_CLASS_PATH" not in text
    assert "importlib.import_module" not in text
