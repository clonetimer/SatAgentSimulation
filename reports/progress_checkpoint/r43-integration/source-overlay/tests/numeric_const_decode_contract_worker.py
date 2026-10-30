"""Real installed LMFE grammar tests; no model weights or GPU generation."""
import copy
import hashlib
import json
from pathlib import Path

import jsonschema
import pytest

from sat_sim_ml.guided_decoding import (_decode_schema, constrained_json_parser,
                                       decoding_evidence, prefix_constraint, response_schema)


STRICT = {'type':'object', 'properties':{'status':{'const':'ok'},'value':{'const':7}},
          'required':['status','value'],'additionalProperties':False}


def fmt(schema):
    return {'type':'json_schema','json_schema':{'schema':schema}}


def accepts(schema, text):
    parser = constrained_json_parser(schema, 256)
    for char in text:
        if char not in parser.get_allowed_characters():
            return False
        parser = parser.add_character(char)
    return parser.can_end()


@pytest.mark.parametrize('value', [0, 7, -3, .25, 1e-7, 9007199254740991])
@pytest.mark.parametrize('typed', [False, True])
def test_numeric_constants_are_enforced_before_final_validation(value, typed):
    schema = {'const':value}
    if typed:
        schema['type'] = 'integer' if type(value) is int else 'number'
    assert accepts(schema, json.dumps(value))
    assert not accepts(schema, json.dumps(value + 1))


@pytest.mark.parametrize('text', ['{"status":"ok","value":7}', '{"value":7,"status":"ok"}'])
def test_original_failing_schema_accepts_both_property_orders(text):
    assert accepts(STRICT,text)


@pytest.mark.parametrize('text', ['{"status":"ok","value":8}', '{"status":"bad","value":7}',
                                 '{"value":7}', '{"status":"ok","value":7,"extra":1}'])
def test_original_schema_rejects_wrong_values_missing_and_extra_fields_at_decode(text):
    assert not accepts(STRICT,text)


def test_original_contract_hash_and_siblings_are_retained():
    schema = {'type':'integer','const':7,'minimum':0,'maximum':10,'multipleOf':1,
              'title':'seven','description':'engineering parameter','default':7}
    before = copy.deepcopy(schema)
    result = response_schema(fmt(schema))
    assert schema == before and result == before
    expected_hash = hashlib.sha256(json.dumps(before,sort_keys=True).encode()).hexdigest()
    assert decoding_evidence(result)['schema_sha256'] == expected_hash
    normalized = _decode_schema(result)
    assert normalized['enum'] == [7] and 'const' not in normalized
    assert {k:v for k,v in normalized.items() if k != 'enum'} == {k:v for k,v in before.items() if k != 'const'}
    assert accepts(result,'7') and not accepts(result,'8')
    assert schema == result == before


def test_const_intersects_existing_enum_without_loosening_it():
    schema = {'type':'number','const':7,'enum':[6,7,8],'minimum':0,'maximum':10}
    assert accepts(schema,'7')
    assert not accepts(schema,'6') and not accepts(schema,'8')
    assert _decode_schema(schema)['enum'] == [7]
    assert schema['enum'] == [6,7,8] and schema['const'] == 7


@pytest.mark.parametrize('siblings', [
    {'minimum':8}, {'maximum':6}, {'exclusiveMinimum':7}, {'exclusiveMaximum':7},
    {'multipleOf':2}, {'type':'string'}, {'enum':[8]}, {'not':{'const':7}},
])
def test_contradictory_numeric_const_rejected_before_tokenizer(siblings):
    schema = {'const':7, **siblings}
    with pytest.raises(ValueError,match='GUIDED_CONST_CONSTRAINT_CONTRADICTION'):
        response_schema(fmt(schema))


def test_conservative_rejection_of_impossible_branch_is_explicit():
    schema = {'anyOf':[{'const':7,'minimum':8},{'const':9}]}
    jsonschema.validate(9,schema)  # Overall schema is viable; no false unsat claim.
    with pytest.raises(ValueError,match='GUIDED_CONST_CONSTRAINT_CONTRADICTION'):
        response_schema(fmt(schema))


@pytest.mark.parametrize('composition', [
    {'anyOf':[{'type':'number'}]}, {'allOf':[{'type':'number'}]},
    {'oneOf':[{'type':'number'}]}, {'$ref':'#/$defs/value'},
])
def test_const_sibling_composition_cannot_bypass_enum_precedence(composition):
    schema = {'const':7, '$defs':{'value':{'type':'number'}}, **composition}
    with pytest.raises(ValueError,match='UNSUPPORTED_GUIDED_NUMERIC_CONST_COMPOSITION'):
        response_schema(fmt(schema))


def test_references_to_numeric_const_definition_keep_exact_literal():
    schema = {'$defs':{'seven':{'const':7}},'$ref':'#/$defs/seven'}
    assert response_schema(fmt(schema)) == schema
    assert accepts(schema,'7') and not accepts(schema,'8')


def test_anyof_numeric_constants_remain_a_union_of_exact_literals():
    schema = {'anyOf':[{'const':7},{'const':9}]}
    assert accepts(schema,'7') and accepts(schema,'9')
    assert not accepts(schema,'8')


@pytest.mark.parametrize('value', [True, False, None, [], [1], {}, {'value':1}])
def test_previously_broken_compound_boolean_and_null_consts_fail_early(value):
    with pytest.raises(ValueError,match='UNSUPPORTED_GUIDED_CONST_TYPE:'):
        response_schema(fmt({'const':value}))


@pytest.mark.parametrize('values', [[True], [False], [True,False], [1,True]])
def test_upstream_non_json_boolean_enums_are_rejected_before_model_work(values):
    with pytest.raises(ValueError,match='UNSUPPORTED_GUIDED_BOOLEAN_ENUM'):
        response_schema(fmt({'enum':values}))


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -float('inf')])
def test_nonfinite_numeric_constants_are_not_turned_into_non_json_tokens(value):
    with pytest.raises(ValueError,match='NONFINITE_GUIDED_NUMERIC_CONST'):
        response_schema(fmt({'const':value}))


def test_defaults_examples_and_property_names_are_not_misread_as_schema_keywords():
    schema = {'type':'object','properties':{'const':{'type':'integer'},
        'enum':{'type':'array','items':{'type':'boolean'}}},
        'default':{'const':True,'enum':[True]},'examples':[{'const':None}],
        'additionalProperties':False}
    assert response_schema(fmt(schema)) == schema
    assert _decode_schema(schema) == schema
    assert accepts(schema,'{"const":1,"enum":[true]}')


def test_plain_json_mode_and_existing_string_const_keep_their_contracts():
    assert response_schema(None) is None
    assert response_schema({'type':'json_object'}) == {'type':'object'}
    assert accepts({'const':'ok'},'"ok"')
    assert not accepts({'const':'ok'},'"bad"')


def test_existing_empty_array_guard_survives_schema_position_walk():
    schema = {'type':'object','properties':{'events':{'type':'array','maxItems':0}},
              'required':['events'],'additionalProperties':False}
    assert accepts(schema,'{"events":[]}')
    assert not accepts(schema,'{"events":[1]}')


def test_existing_numeric_bounds_still_reject_values_before_termination():
    schema = {'type':'object','properties':{'value':{'type':'number','minimum':.2,'maximum':.8}},
              'required':['value'],'additionalProperties':False}
    assert accepts(schema,'{"value":0.3}')
    assert not accepts(schema,'{"value":0.1}')
    assert not accepts(schema,'{"value":1}')


class TokenSequence(list):
    def tolist(self):
        return list(self)


def test_actual_local_tokenizer_lmfe_hook_accepts_exact_json_and_blocks_wrong_numeric_token():
    import torch
    from transformers import AutoTokenizer
    assert not torch.cuda.is_initialized()
    manifests = [parent/'.models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json'
                 for parent in Path(__file__).resolve().parents]
    manifest_path = next((path for path in manifests if path.is_file()),None)
    if manifest_path is None:
        pytest.skip('optional local tokenizer snapshot is not installed')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    tokenizer = AutoTokenizer.from_pretrained(manifest['snapshot'],local_files_only=True,trust_remote_code=False)
    original = copy.deepcopy(STRICT)
    hook = prefix_constraint(tokenizer,STRICT,64)
    prompt = tokenizer.encode('Return JSON.',add_special_tokens=False)

    def token_path_allowed(text):
        sent = TokenSequence(prompt)
        for token in tokenizer.encode(text,add_special_tokens=False):
            allowed = hook(0,sent)
            if token not in allowed:
                return False, sent
            sent.append(token)
        return True, sent

    valid, complete = token_path_allowed('{"status":"ok","value":7}')
    assert valid and tokenizer.eos_token_id in hook(0,complete)
    invalid, _ = token_path_allowed('{"status":"ok","value":8}')
    assert not invalid
    assert STRICT == original
    assert not torch.cuda.is_initialized()  # No model or GPU allocations.


def test_current_taskspec_schemas_do_not_hit_new_support_rejections():
    from sat_sim.capability_registry import active_capability_ids
    from sat_sim.llm_backends import taskspec_draft_response_schema
    for capability in (None,*active_capability_ids(real_llm_eval=True)):
        for forbid in (False,True):
            for geometry in (False,True):
                for wheels in (False,True):
                    schema = taskspec_draft_response_schema(capability,forbid_events=forbid,
                        allow_geometry_overrides=geometry,allow_per_wheel_overrides=wheels)
                    assert _decode_schema(schema) == schema


@pytest.mark.parametrize('forbid', [False,True])
def test_current_whole_spacecraft_taskspec_runs_through_actual_lmfe_parser(forbid):
    from sat_sim.llm_backends import taskspec_draft_response_schema
    capability = 'whole_spacecraft.unified_native.v1'
    schema = taskspec_draft_response_schema(capability,forbid_events=forbid)
    draft = {'schema_version':'0.1.0','task_id':'t','task_type':'whole_spacecraft',
        'target':{},'simulation':{},'outputs':{},'capability_id':capability,'parameters':{}}
    jsonschema.validate(draft,schema)
    assert accepts(schema,json.dumps(draft,separators=(',',':')))
