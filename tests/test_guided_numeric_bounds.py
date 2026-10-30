"""The optional serving decoder is checked in its actual Python environment."""
from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys

import pytest


def test_guided_numeric_bounds_reject_invalid_emitted_values():
    root = Path(__file__).resolve().parents[1]
    python = Path(sys.executable)
    if importlib.util.find_spec('lmformatenforcer') is None:
        python = root / '.venv-sft' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        if not python.is_file():
            pytest.skip('optional serving environment with lm-format-enforcer is not installed')
    result = subprocess.run([str(python), str(root / 'tests/guided_numeric_bounds_worker.py')],
        cwd=root, env={**os.environ, 'PYTHONUTF8': '1'}, capture_output=True, text=True, encoding='utf-8', timeout=60)
    assert result.returncode == 0, result.stderr
    proof = json.loads(result.stdout)
    assert proof['status'] == 'PASS'
    assert proof['numeric_prefix_cases'] >= 100
    assert proof['string_and_nested_array_cases'] == 3
    assert proof['tokenizer_alphabet_propagates_through_union'] is True
    assert proof['invalid_mantissa_makes_bounded_progress'] is True
    assert proof['explicit_no_event_array_cannot_start_an_item'] is True
    assert proof['repeated_empty_event_arrays_do_not_multiply_branches'] is True
