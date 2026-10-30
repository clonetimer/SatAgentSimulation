"""Downloaded JSON output is UTF-8 even under a legacy Windows pipe codec."""
import json
import os
import subprocess
import sys

import pytest

from sat_sim.form_schema import capability_form_schema
from sat_sim.script_exporter import export_runner_script
from sat_sim.unified_agent import normalize_form_task_spec


@pytest.mark.parametrize('kind', ['python', 'capability-python'])
def test_exported_script_unicode_survives_legacy_console_encoding(tmp_path, kind):
    form = capability_form_schema('component.thermal_node.v1')['default_form']
    spec = normalize_form_task_spec(form)
    marker = '温度验证🛰'
    spec['model']['validation'] = {'assertions':[
        {'id': marker, 'field':'thermal.node.temp_k', 'operator':'>', 'value':0.}]}
    source = tmp_path/'task.json'
    source.write_text(json.dumps(spec,ensure_ascii=False),encoding='utf-8')
    script = export_runner_script(source,tmp_path/'download.py',kind=kind,output_root=tmp_path/'data')
    env = {**os.environ,'PYTHONIOENCODING':'cp936','PYTHONUTF8':'0'}
    result = subprocess.run([sys.executable,str(script.output_path)],cwd=tmp_path,env=env,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=60)
    text = result.stdout.decode('utf-8',errors='strict')
    assert result.returncode == 0, result.stderr.decode('utf-8',errors='replace')
    payload = json.loads(text)
    assert payload['ok'] is True
    assert marker in text
