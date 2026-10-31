"""Exercise optional real serving dependencies without installing ML into core."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def test_finite_json_constants_in_actual_serving_environment(tmp_path):
    candidate = Path(__file__).resolve().parents[1]
    workspace = next((path for path in (candidate, *candidate.parents) if (path / '.venv-sft').is_dir()), None)
    if workspace is None:
        pytest.skip('optional local serving environment is not installed')
    python = workspace / '.venv-sft' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        pytest.skip('optional local serving Python is not installed')
    tokenizer = workspace / '.models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json'
    if not tokenizer.is_file():
        pytest.skip('optional local tokenizer is not installed')
    worker = candidate / 'tests/finite_json_const_decode_worker.py'
    report = tmp_path / 'finite-json-const-worker.json'
    child = subprocess.run([str(python), '-I', '-B', str(worker), '--source', str(candidate / 'src'),
                            '--workspace', str(workspace), '--report', str(report)],
                           cwd=tmp_path, capture_output=True, text=True, encoding='utf-8', timeout=90,
                           env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'HF_HUB_OFFLINE': '1',
                                'TRANSFORMERS_OFFLINE': '1', 'TOKENIZERS_PARALLELISM': 'false'})
    assert child.returncode == 0, child.stdout + '\n' + child.stderr
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['status'] == 'PASS_CPU_CONST_GRAMMAR_ONLY'
    assert result['tests_run'] == 17
    assert result['failures'] == result['errors'] == result['skipped'] == 0
    assert result['source_bytes_unchanged'] is True
    assert result['cuda_initialized'] is False
    assert result['audit']['socket'] == result['audit']['weights'] == 0
