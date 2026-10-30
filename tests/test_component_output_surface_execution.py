"""Actual default component execution must agree with contracts and UI fields."""
import json
from pathlib import Path
import subprocess
import sys


def test_all_component_default_numeric_outputs_are_declared_and_selectable(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path/'output-surface.json'
    completed = subprocess.run([sys.executable,str(root/'scripts/audit_capability_output_surfaces.py'),
                                '--output',str(output)],cwd=root,capture_output=True,text=True,
                               encoding='utf-8',timeout=120,check=False)
    assert completed.returncode==0,completed.stdout+'\n'+completed.stderr
    report = json.loads(output.read_text(encoding='utf-8'))
    assert len(report['capabilities'])==24
    assert report['status']=='PASS'
    for component in report['capabilities']:
        assert component['status']=='EXECUTED'
        assert component['trace_row_count']>1
        assert component['trace_fields']
        assert not component['numeric_trace_not_declared']
        assert not component['numeric_trace_not_in_form']
        assert not component['summary_qoi_not_declared']
