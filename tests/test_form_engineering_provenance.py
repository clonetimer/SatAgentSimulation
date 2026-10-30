"""A fully submitted form still needs a complete input-origin ledger."""
from copy import deepcopy

from sat_sim.form_schema import capability_form_schema
from sat_sim.unified_agent import normalize_form_task_spec
from sat_sim.task_validator import validate_task_spec


def test_submitted_default_form_records_every_parameter_without_promoting_confidence():
    form = deepcopy(capability_form_schema('whole_spacecraft.unified_native.v1')['default_form'])
    spec = normalize_form_task_spec(form)
    paths = {f'parameters.values.{name}' for name in form['parameters']['values']}
    assert paths <= set(spec['provenance']['fields'])
    assert paths <= {item['path'] for item in spec['provenance']['assumptions']}
    for path in paths:
        assert spec['provenance']['fields'][path]['source'] == 'user_provided'
        assert spec['provenance']['fields'][path]['evidence']
    assert spec['parameters']['profile'] == 'demo'
    assert spec['assurance']['claim_level'] == 'analysis_only'
    validate_task_spec(spec).raise_for_errors()


def test_missing_form_parameter_is_distinguished_from_submitted_value():
    form = deepcopy(capability_form_schema('whole_spacecraft.unified_native.v1')['default_form'])
    del form['parameters']['values']['spacecraft_mass_kg']
    spec = normalize_form_task_spec(form)
    field = spec['provenance']['fields']['parameters.values.spacecraft_mass_kg']
    assert field['source'] == 'form_default'
    assert 'whole_spacecraft.unified_native.v1.parameters.spacecraft_mass_kg' in field['evidence']
    assert spec['parameters']['values']['spacecraft_mass_kg'] == 100.


def test_explicit_canonical_parameter_sources_are_preserved():
    form = deepcopy(capability_form_schema('whole_spacecraft.unified_native.v1')['default_form'])
    spec = normalize_form_task_spec(form)
    origin = {'source':'user_explicit', 'evidence':'Declared generic design assumption, revision 1', 'note':'100 kg reference spacecraft'}
    spec['provenance']['fields']['parameters.values.spacecraft_mass_kg'] = origin
    normalized = normalize_form_task_spec(spec)
    assert normalized['provenance']['fields']['parameters.values.spacecraft_mass_kg'] == origin


def test_engineering_scope_survives_template_projection_and_form_edits():
    from sat_sim.form_schema import task_spec_to_form_data
    from sat_sim.scenario_templates import instantiate_scenario_template
    spec = instantiate_scenario_template('generic_leo_small_sat_engineering')
    spec['metadata']['agent'] = {'actual_model_used':True}
    form = task_spec_to_form_data('whole_spacecraft.unified_native.v1', spec)['form_data']
    assert 'agent' not in form.get('metadata', {})
    form['simulation']['duration_s'] = 20.
    rebuilt = normalize_form_task_spec(form)
    assert rebuilt['simulation']['duration_s'] == 20.
    assert rebuilt['metadata']['engineering_scope'] == spec['metadata']['engineering_scope']
    assert rebuilt['metadata']['scenario_template']['template_id'] == 'generic_leo_small_sat_engineering'
