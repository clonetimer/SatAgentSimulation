"""The physical thermal-link control is also available to offline/agent forms."""
import copy

from sat_sim.adapters.whole_spacecraft_unified_native import WholeSpacecraftUnifiedNativeAdapter
from sat_sim.form_schema import capability_form_schema
from sat_sim.task_models import to_runtime_task_spec
from sat_sim.unified_agent import normalize_form_task_spec


def test_thermal_link_offline_form_contract_and_native_parameter_roundtrip():
    key = 'solar_panel_structure_conductance_w_k'
    schema = capability_form_schema('whole_spacecraft.unified_native.v1')
    field = next(f for f in schema['fields'] if f['path'] == 'parameters.values.' + key)
    assert field['label'] == '太阳翼—结构等效导热系数'
    assert field['unit'] == 'W/K'
    assert (field['default'], field['minimum'], field['maximum']) == (2., 0., 20.)
    form = copy.deepcopy(schema['default_form'])
    form['simulation'].update(duration_s=3., sample_s=1., step_s=.2)
    form['parameters']['values'][key] = 4.
    form['parameters']['values']['environment_profile'] = 'spice_wmm'
    canonical = normalize_form_task_spec(form, task_id='thermal-link-offline-roundtrip')
    assert canonical['parameters']['values'][key] == 4.
    runtime = to_runtime_task_spec(canonical)
    # The established runtime adapter format flattens canonical values.
    assert runtime['parameters'][key] == 4.
    adapter = WholeSpacecraftUnifiedNativeAdapter()
    assert not [issue for issue in adapter.validate(runtime) if issue.severity == 'error']
    actual = adapter.run(runtime)
    links = [p for p in actual.summary['thermal_conduction_paths']
             if {p['from_node'], p['to_node']} == {'solar_panel', 'structure'}]
    assert len(links) == 1 and links[0]['conductance_w_per_k'] == 4.
    assert actual.summary['execution_status'] == 'PASS'
