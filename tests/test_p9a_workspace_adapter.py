"""Cross-language contract check using the real Python object-tree output."""
import json
from pathlib import Path
import subprocess

from sat_sim.form_schema import capability_form_schema
from sat_sim.workbench_catalog import workbench_presentation_catalog
from sat_sim.workbench_scene_catalog import build_scene_catalog
from sat_sim.workbench_object_tree import build_object_tree
from sat_sim.performance_evaluation import evaluate_snapshot


def test_workspace_adapter_consumes_the_real_tree_without_summary_nodes(tmp_path):
    module = Path('src/sat_sim/web/object-workspace.js')
    assert module.is_file(), 'object workspace adapter is missing'
    scene = build_scene_catalog(workbench_presentation_catalog())
    schemas = {o['execution_capability_id']: capability_form_schema(o['execution_capability_id'])
               for o in scene['objects'] if o.get('execution_capability_id')}
    binding = {'owner_object_id': 'component.reaction_wheel', 'source_capability_id': 'component.reaction_wheel.v1',
               'source_field': 'adcs.reaction_wheel.speed_rad_s_0', 'unit': 'rad/s'}
    snapshot = {'schema_version': 'recorded-snapshot.v1', 'run_id': 'run-1', 'snapshot_id': 's1', 'sealed': True,
                'run_status': 'SUCCEEDED', 'series': {'speed': {**binding, 'times_s': [0, 1], 'values': [1, 2]}}}
    plan = {'schema_version': 'evaluation-plan.v1', 'plan_id': 'p1', 'version': '1', 'window_s': [0, 1],
            'metrics': [{'metric_id': 'max', 'display_name_zh': '反作用轮 1 · 最大绝对角速度',
                         'series_id': 'speed', **binding, 'formula': 'max_abs.v1', 'limits': {'upper': 1}}]}
    fixture = tmp_path / 'real-workspace-contract.json'
    fixture.write_text(json.dumps({'tree': build_object_tree(scene, schemas), 'report': evaluate_snapshot(snapshot, plan)},
                                 ensure_ascii=False), encoding='utf-8')
    process = subprocess.run(['node', 'tests/js/object_workspace_vm.js', str(fixture)],
                             text=True, capture_output=True, timeout=15, encoding='utf-8')
    assert process.returncode == 0, process.stdout + process.stderr
