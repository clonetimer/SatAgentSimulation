"""Use the actual optional serving env without adding ML deps to the core env."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest


def test_numeric_const_grammar_in_actual_serving_environment(tmp_path):
    target = Path(__file__).resolve().parents[1]
    ancestors = list(target.parents)
    owner = next((path for path in (target,*ancestors) if (path/'.venv-sft').is_dir()),target)
    python = Path(sys.executable)
    if importlib.util.find_spec('lmformatenforcer') is None:
        python = owner/'.venv-sft'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
        if not python.is_file():
            pytest.skip('optional serving environment with lm-format-enforcer is not installed')
    support = target
    if not (support/'src/sat_sim/llm_backends.py').is_file():
        support = owner/'reports/local_requirements_completion/requirements-r24-integrated-candidate'
    worker = target/'tests/numeric_const_decode_contract_worker.py'
    xml = tmp_path/'numeric-const-worker.xml'
    config = target/('pytest.ini' if (target/'pytest.ini').is_file() else 'pyproject.toml')
    code = '''import os,sys
sys.dont_write_bytecode=True
sys.path[:0]=[sys.argv[1],sys.argv[2]]
sys.path.append(sys.argv[3])
import psutil
psutil.Process().cpu_affinity([0,1,2,3])
import pytest
raise SystemExit(pytest.main([sys.argv[4],'-c',sys.argv[5],'-q','-p','no:cacheprovider',
    '--noconftest','--junitxml='+sys.argv[6]]))
'''
    result = subprocess.run([str(python),'-c',code,str(target/'src'),str(support/'src'),
        str(Path(pytest.__file__).resolve().parents[1]),str(worker),str(config),str(xml)],cwd=target,
        env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1',
             'TOKENIZERS_PARALLELISM':'false','OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1'},
        capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0, result.stdout+'\n'+result.stderr
    suite = ET.parse(xml).getroot().find('testsuite')
    assert int(suite.attrib['tests'])==57
    assert int(suite.attrib['failures'])==int(suite.attrib['errors'])==0
    available = any((path/'.models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json').is_file()
                    for path in (target,*ancestors))
    assert int(suite.attrib['skipped'])==(0 if available else 1)
