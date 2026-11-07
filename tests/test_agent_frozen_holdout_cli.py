"""Frozen request file handling for the real-model A/B runner.

This only verifies the CLI preserves the operator's frozen case strings. It
does not run or replace the frozen real-model acceptance results.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "run_experience_longitudinal_eval.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("sat_sim_frozen_eval_cli_test", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cli_preserves_verbatim_frozen_holdout_inputs(tmp_path, monkeypatch, capsys):
    cli = _load_cli()
    frozen = [
        "电池仿真 8秒、每2秒采样",
        "Use mode=default and duration 10 seconds.",
        "保持默认配置，运行12秒。",
        "capacity_wh=160、initial_soc=0.65",
        "Use component.battery.v1 with default settings.",
        "仿真16秒. 输出电池状态。",
    ]
    request_file = tmp_path / "frozen.json"
    request_file.write_text(json.dumps(frozen, ensure_ascii=False), encoding="utf-8")
    called = {}

    def fake_eval(**kwargs):
        called.update(kwargs)
        return {"status": "PASS"}

    monkeypatch.setattr(cli, "run_experience_longitudinal_eval", fake_eval)
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT_PATH),
        "--source-bundle", str(tmp_path / "source"),
        "--requests-file", str(request_file),
        "--output-dir", str(tmp_path / "results"),
    ])
    assert cli.main() == 0
    assert list(called["requests"]) == frozen
    assert called["output_dir"] == tmp_path / "results"
    assert '"status": "PASS"' in capsys.readouterr().out


@pytest.mark.parametrize("frozen", [
    {},
    [1, 2, 3, 4, 5, 6],
    ["a", "b", "c", "d", "e"],
    ["a", "b", "c", "d", "e", "   "],
])
def test_cli_rejects_invalid_or_changed_holdout_shape(tmp_path, monkeypatch, frozen):
    cli = _load_cli()
    request_file = tmp_path / "invalid.json"
    request_file.write_text(json.dumps(frozen), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", [
        str(SCRIPT_PATH),
        "--source-bundle", str(tmp_path / "source"),
        "--requests-file", str(request_file),
    ])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2
