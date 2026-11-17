from __future__ import annotations

from pathlib import Path


def test_run_result_presents_physics_backend_and_native_provenance() -> None:
    source = (Path(__file__).resolve().parents[1] / "src/sat_sim/web/app.js").read_text(encoding="utf-8")
    assert "function runPhysicsSourceDetails(run)" in source
    assert "Basilisk 官方原生" in source
    assert "项目参考模型（非 Basilisk 原生）" in source
    assert "模型来源标识" in source
    assert "运行证据" in source
    assert "...runPhysicsSourceDetails(run)" in source
