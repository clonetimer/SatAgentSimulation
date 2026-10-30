"""CPU-only acceptance of emitted JSON prefixes in the optional serving env."""
from pathlib import Path
import json
import sys
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from sat_sim_ml.guided_decoding import constrained_json_parser


def accepts(schema, text):
    parser = constrained_json_parser(schema, 4096)
    for character in text:
        if character not in parser.get_allowed_characters():
            return False
        parser = parser.add_character(character)
    return parser.can_end()


def main():
    cases = []
    for numeric, valid, invalid in [
        ({'type': 'number', 'minimum': 0, 'maximum': .2},
         ['0', '0.1', '0.2', '50e-3', '2e-1'], ['50', '-0.1', '0.200001', '2.01e-1']),
        ({'type': 'number', 'minimum': 1e-6},
         ['0.000001', '1e-6', '50'], ['0', '0.0000009', '1e-7']),
        ({'type': 'number', 'exclusiveMinimum': -1, 'exclusiveMaximum': 1},
         ['0', '-0.1', '0.99999'], ['-1', '1', '2']),
        ({'type': 'number', 'multipleOf': .1},
         ['0', '0.1', '0.3', '-0.2'], ['0.11', '0.301']),
        ({'type': 'integer', 'minimum': 1, 'maximum': 20},
         ['1', '20'], ['0', '21', '-1', '1.5']),
    ]:
        for value in valid + invalid:
            for layout in ['root', 'object', 'array', 'wrapped']:
                if layout == 'root':
                    schema, text = numeric, value
                elif layout == 'object':
                    schema = {'type': 'object', 'properties': {'value': numeric}, 'required': ['value']}
                    text = '{"value":' + value + '}'
                elif layout == 'array':
                    schema, text = {'type': 'array', 'items': numeric}, '[' + value + ']'
                else:
                    schema = {'anyOf': [{'type': 'object', 'properties': {'value': numeric}, 'required': ['value']},
                                        {'type': 'object', 'properties': {'other': {'type': 'boolean'}}, 'required': ['other']}]}
                    text = '{"value":' + value + '}'
                expected = value in valid
                actual = accepts(schema, text)
                assert actual == expected, (schema, text, actual, expected)
                cases.append((layout, text, actual))
    schema = {'type': 'object', 'properties': {
        'text': {'type': 'string'}, 'values': {'type': 'array', 'items': {'type': 'number', 'minimum': 0, 'maximum': .2}}},
        'required': ['text', 'values'], 'additionalProperties': False}
    assert accepts(schema, '{"text":"50, } [ \\" end", "values":[0.1,50e-3,0.2]}')
    assert not accepts(schema, '{"text":"ok", "values":[0.1,50,0.2]}')
    assert not accepts(schema, '{"text":"ok", "values":[0.1,50 ,0.2]}')
    # TokenEnforcer replaces the root alphabet after parser construction.
    # All retained root references, including anyOf arms, must receive it.
    unicode_schema = {'anyOf': [schema, {'type': 'object', 'properties': {'other': {'type': 'boolean'}}, 'required': ['other']}]}
    parser = constrained_json_parser(unicode_schema, 4096)
    parser.config = replace(parser.config, alphabet=parser.config.alphabet + '\u5de5\u7a0b\u4eff\u771f')
    for character in '{"text":"\u5de5\u7a0b\u4eff\u771f", "values":[0.1]}':
        assert character in parser.get_allowed_characters(), character
        parser = parser.add_character(character)
    assert parser.can_end()
    from sat_sim.llm_backends import taskspec_draft_response_schema
    no_event_schema = taskspec_draft_response_schema('whole_spacecraft.unified_native.v1', forbid_events=True)
    parser = constrained_json_parser(no_event_schema, 4096)
    for character in ('{"schema_version":"0.1.0","task_id":"t","task_type":"whole_spacecraft",'
                      '"capability_id":"whole_spacecraft.unified_native.v1","target":{},"simulation":{},'
                      '"outputs":{},"modifiers":{"degradations":['):
        assert character in parser.get_allowed_characters(), character
        parser = parser.add_character(character)
    assert ']' in parser.get_allowed_characters()
    assert '{' not in parser.get_allowed_characters()
    parser = constrained_json_parser(no_event_schema, 4096)
    for character in ('{"schema_version":"0.1.0","task_id":"t","task_type":"whole_spacecraft",'
                      '"capability_id":"whole_spacecraft.unified_native.v1","target":{},"simulation":{},'
                      '"faults":[],"modifiers":{"faults":[],"degradations":[],"constraints":[]},'
                      '"outputs":{"output_root":"datasets/generated_task","'):
        assert character in parser.get_allowed_characters(), character
        parser = parser.add_character(character)
    from lmformatenforcer.characterlevelparser import UnionParser
    def leaf_count(node):
        if hasattr(node, 'delegate'):
            return leaf_count(node.delegate)
        return sum(leaf_count(child) for child in node.parsers) if isinstance(node, UnionParser) else 1
    assert leaf_count(parser) <= 8, leaf_count(parser)
    progress_schema = {'type': 'object', 'properties': {'value': {
        'type': 'number', 'minimum': 1e-6, 'maximum': .2}}, 'required': ['value'], 'additionalProperties': False}
    parser = constrained_json_parser(progress_schema, 4096)
    for character in '{"value":50.000000000000000':
        assert character in parser.get_allowed_characters(), character
        parser = parser.add_character(character)
    assert not parser.can_end()
    assert set(parser.get_allowed_characters()) <= set('eE')
    assert 'e' in parser.get_allowed_characters()
    parser = parser.add_character('e')
    assert set(parser.get_allowed_characters()) == {'-'}
    parser = parser.add_character('-')
    assert '0' not in parser.get_allowed_characters()  # no unbounded exponent zero padding
    assert '1' not in parser.get_allowed_characters()  # no exponent prefix with no legal completion
    assert '3' in parser.get_allowed_characters()
    for character in '3}':
        assert character in parser.get_allowed_characters(), character
        parser = parser.add_character(character)
    assert parser.can_end()
    print(json.dumps({'status': 'PASS', 'numeric_prefix_cases': len(cases), 'string_and_nested_array_cases': 3,
                      'tokenizer_alphabet_propagates_through_union': True, 'invalid_mantissa_makes_bounded_progress': True,
                      'explicit_no_event_array_cannot_start_an_item': True,
                      'repeated_empty_event_arrays_do_not_multiply_branches': True}))


if __name__ == '__main__':
    main()
