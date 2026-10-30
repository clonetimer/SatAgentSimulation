"""Endurance provenance must include physics and the validators judging it."""
from pathlib import Path
import runpy

import pytest


SCRIPT = Path(__file__).resolve().parents[1]/'scripts/run_native_endurance.py'


def _source_tree(root, module):
    for folder in module['SOURCE_DIRECTORIES']:
        path = root/folder/'example.py'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('original = True\n', encoding='utf-8')
    for relative in module['SOURCE_FILES']:
        path = root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('original = True\n', encoding='utf-8')


@pytest.mark.parametrize('relative',[
    'src/sat_sim/bsk_engine/example.py',
    'src/sat_sim/interactive/example.py',
    'src/sat_sim/validation/example.py',
    'src/sat_sim/validation_outcome.py',
    'src/sat_sim/assertions.py',
    'src/sat_sim/effect_evidence.py',
    'src/sat_sim/evidence/effect_evidence_v32.json',
    'src/sat_sim/run_bundle.py',
    'src/sat_sim/thermal_environment.py',
    'src/sat_sim/thermal_quadrature.py',
    'requirements-lock.txt',
])
def test_validator_or_physics_change_invalidates_same_endurance_revision(tmp_path, relative):
    module = runpy.run_path(str(SCRIPT))
    _source_tree(tmp_path, module)
    manifest = module['runtime_source_manifest'](tmp_path)
    before = module['runtime_source_fingerprint'](manifest)
    (tmp_path/relative).write_text('original = False\n', encoding='utf-8')
    after = module['runtime_source_fingerprint'](module['runtime_source_manifest'](tmp_path))
    assert after != before
    assert manifest['scheme'] == 'runtime-and-validation-v6'


@pytest.mark.parametrize('relative',['src/sat_sim/run_bundle.py','src/sat_sim/assertions.py'])
def test_missing_validator_is_not_silently_excluded(tmp_path,relative):
    module = runpy.run_path(str(SCRIPT))
    _source_tree(tmp_path, module)
    (tmp_path/relative).unlink()
    with pytest.raises(FileNotFoundError, match='ENDURANCE_VALIDATOR_SOURCE_MISSING'):
        module['runtime_source_manifest'](tmp_path)


def test_presentation_edits_do_not_change_physics_or_validation_manifest(tmp_path):
    module = runpy.run_path(str(SCRIPT))
    _source_tree(tmp_path, module)
    before = module['runtime_source_fingerprint'](module['runtime_source_manifest'](tmp_path))
    path = tmp_path/'src/sat_sim/web/app.js'
    path.parent.mkdir(parents=True)
    path.write_text('const presentationOnly = true;', encoding='utf-8')
    assert module['runtime_source_fingerprint'](module['runtime_source_manifest'](tmp_path)) == before


@pytest.mark.parametrize('relative',[
    'src/sat_sim/dataset_writer.py','src/sat_sim/api.py',
    'src/sat_sim/web/app.js','src/sat_sim/capabilities/example.yaml',
    'src/parameters/data/example.json','requirements-sft-lock.txt',
])
def test_application_binding_detects_export_ui_and_packaged_default_edits(tmp_path,relative):
    module=runpy.run_path(str(SCRIPT))
    _source_tree(tmp_path,module)
    path=tmp_path/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text('original',encoding='utf-8')
    before=module['application_source_manifest'](tmp_path)
    assert before['scheme']=='application-source-v1'
    path.write_text('modified',encoding='utf-8')
    after=module['application_source_manifest'](tmp_path)
    assert module['runtime_source_fingerprint'](before)!=module['runtime_source_fingerprint'](after)


def test_application_binding_ignores_only_generated_bytecode_caches(tmp_path):
    module=runpy.run_path(str(SCRIPT))
    _source_tree(tmp_path,module)
    before=module['application_source_manifest'](tmp_path)
    cache=tmp_path/'src/sat_sim/__pycache__/example.cpython-312.pyc'
    cache.parent.mkdir(parents=True,exist_ok=True)
    cache.write_bytes(b'generated cache')
    assert module['application_source_manifest'](tmp_path)==before
