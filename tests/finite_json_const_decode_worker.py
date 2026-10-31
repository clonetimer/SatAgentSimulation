"""Optional serving-env CPU tests for finite const grammar, without model weights."""
from __future__ import annotations
import argparse
import copy
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import sys
import unittest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    source = args.source.resolve()
    workspace = args.workspace.resolve()
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(source))
    os.environ.update({'CUDA_VISIBLE_DEVICES': '', 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                       'TOKENIZERS_PARALLELISM': 'false', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1'})
    audit_counts = {'socket': 0, 'weights': 0, 'os_version_queries': 0}

    def audit(event, values):
        if event == 'socket.connect':
            audit_counts['socket'] += 1
            raise RuntimeError('CPU const worker forbids sockets')
        if event == 'open' and isinstance(values[0], str) and '.models' in Path(values[0]).parts and Path(values[0]).suffix in {'.bin', '.pt', '.pth', '.safetensors'}:
            audit_counts['weights'] += 1
            raise RuntimeError('CPU const worker forbids model weights')
        if event == 'subprocess.Popen':
            if Path(str(values[0])).name.lower() == 'cmd.exe' and re.fullmatch(re.escape(str(values[0])) + r' /c "ver"', str(values[1]), re.I):
                audit_counts['os_version_queries'] += 1
                return
            raise RuntimeError('CPU const worker forbids subprocesses except stdlib OS version query')
    sys.addaudithook(audit)
    import psutil
    psutil.Process().cpu_affinity(psutil.Process().cpu_affinity()[:2])
    from jsonschema import Draft202012Validator
    from sat_sim_ml import guided_decoding, bounded_numeric_parser
    from sat_sim_ml.guided_decoding import _decode_schema, constrained_json_parser, decoding_evidence, prefix_constraint, response_schema
    assert Path(guided_decoding.__file__).resolve().is_relative_to(source)
    assert Path(bounded_numeric_parser.__file__).resolve().is_relative_to(source)
    guarded = [Path(guided_decoding.__file__).resolve(), Path(bounded_numeric_parser.__file__).resolve()]

    def digest(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()
    before = {str(path): digest(path) for path in guarded}

    def accepts(schema, text):
        state = constrained_json_parser(schema, 512)
        for char in text:
            if char not in state.get_allowed_characters():
                return False
            state = state.add_character(char)
        return state.can_end()

    def fmt(schema):
        return {'type': 'json_schema', 'json_schema': {'schema': schema}}

    def literal(value):
        return json.dumps(value, separators=(',', ':'), ensure_ascii=True, allow_nan=False)

    class FiniteConstTests(unittest.TestCase):
        def test_top_level_supported_constants(self):
            for value in (True, False, None, [], [1, 2], {}, {'a': [True, None, 1e-5]},
                          ['quote"', 'slash\\', 'line\n', '\u4e2d\U0001f680']):
                with self.subTest(value=value):
                    schema = {'const': value}
                    self.assertEqual(response_schema(fmt(schema)), schema)
                    self.assertTrue(accepts(schema, literal(value)))
                    other = [1] if value != [1] else [2]
                    self.assertFalse(accepts(schema, literal(other)))

        def test_typed_constants_with_sibling_constraints(self):
            rows = [(True, {'type': 'boolean', 'enum': [False, True]}),
                    (None, {'type': 'null'}),
                    ([1, 2], {'type': 'array', 'items': {'type': 'integer', 'minimum': 0}, 'minItems': 2, 'maxItems': 2}),
                    ({'a': 1}, {'type': 'object', 'properties': {'a': {'type': 'integer', 'minimum': 0}}, 'required': ['a'], 'additionalProperties': False})]
            for value, siblings in rows:
                with self.subTest(value=value):
                    schema = {'const': value, **siblings}
                    original = copy.deepcopy(schema)
                    self.assertTrue(accepts(schema, literal(value)))
                    self.assertEqual(schema, original)
                    self.assertEqual(response_schema(fmt(schema)), original)
                    expected = hashlib.sha256(json.dumps(original, sort_keys=True).encode()).hexdigest()
                    self.assertEqual(decoding_evidence(schema)['schema_sha256'], expected)

        def test_object_properties_and_array_items_use_exact_const(self):
            schema = {'type': 'object', 'properties': {
                'flag': {'type': 'boolean', 'const': True},
                'values': {'type': 'array', 'items': {'const': {'a': [1, False]}}, 'minItems': 1, 'maxItems': 1}},
                'required': ['flag', 'values'], 'additionalProperties': False}
            self.assertTrue(accepts(schema, '{"flag":true,"values":[{"a":[1,false]}]}'))
            for wrong in ('{"flag":false,"values":[{"a":[1,false]}]}',
                          '{"flag":true,"values":[{"a":[1,true]}]}',
                          '{"flag":true,"values":[]}',
                          '{"flag":true,"values":[{"a":[1,false]},{"a":[1,false]}]}'):
                with self.subTest(wrong=wrong):
                    self.assertFalse(accepts(schema, wrong))

        def test_exact_object_const_does_not_allow_key_value_mutation(self):
            schema = {'const': {'a': 1, 'b': [True, None]}}
            self.assertTrue(accepts(schema, '{"a":1,"b":[true,null]}'))
            for wrong in ('{"a":1,"b":[false,null]}', '{"a":1}', '{"a":1,"b":[true,null],"c":0}',
                          '{"a":1,"a":1,"b":[true,null]}'):
                self.assertFalse(accepts(schema, wrong))

        def test_prefix_cannot_end_and_invalid_character_is_rejected(self):
            schema = {'const': {'a': [True, None]}}
            text = literal(schema['const'])
            for end in range(len(text)):
                with self.subTest(end=end):
                    self.assertFalse(accepts(schema, text[:end]))
            self.assertTrue(accepts(schema, ' \n' + text + ' \t'))
            self.assertFalse(accepts(schema, text + 'false'))

        def test_nested_escaped_string_is_constrained_as_json_text(self):
            value = ['quote"', 'slash\\', 'line\n', '\u4e2d\U0001f680']
            text = literal(value)
            self.assertTrue(accepts({'const': value}, text))
            self.assertFalse(accepts({'const': value}, text.replace('quote', 'wrong')))
            self.assertFalse(accepts({'const': value}, text.replace('\\n', '\n')))

        def test_numeric_const_path_still_preserves_type_bounds_and_enum(self):
            schema = {'type': 'number', 'minimum': 0.0, 'maximum': 1.0, 'enum': [1e-5, 1e-4], 'const': 1e-5}
            decoded = _decode_schema(schema)
            self.assertEqual(decoded, {key: value for key, value in {**schema, 'enum': [1e-5]}.items() if key != 'const'})
            self.assertTrue(accepts(schema, '1e-05'))
            for wrong in ('1e-15', '1e-04', 'true', '0', '"1e-05"'):
                self.assertFalse(accepts(schema, wrong))

        def test_bool_is_not_numeric_and_sibling_contradictions_fail_early(self):
            rows = [{'type': 'number', 'const': True}, {'type': 'boolean', 'const': 1},
                    {'type': 'array', 'const': [1, -1], 'items': {'type': 'number', 'minimum': 0}},
                    {'const': {'a': 1}, 'required': ['b']}, {'const': True, 'enum': [False]},
                    {'const': None, 'type': 'string'}, {'type': 'integer', 'const': 1.5}]
            for schema in rows:
                with self.subTest(schema=schema), self.assertRaisesRegex(ValueError, 'GUIDED_CONST_CONSTRAINT_CONTRADICTION'):
                    response_schema(fmt(schema))

        def test_recursive_nonfinite_values_and_non_json_keys_are_rejected(self):
            for value in ([float('nan')], {'a': float('inf')}, {'a': [float('-inf')]}):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'NONFINITE_GUIDED_NUMERIC_CONST'):
                    _decode_schema({'const': value})
            with self.assertRaisesRegex(ValueError, 'KEYS_MUST_BE_STRINGS'):
                _decode_schema({'const': {1: 'one'}})
            cyclic = []
            cyclic.append(cyclic)
            with self.assertRaisesRegex(ValueError, 'GUIDED_CONST_CYCLE'):
                _decode_schema({'const': cyclic})

        def test_size_and_depth_boundaries_fail_explicitly(self):
            nested = None
            for _ in range(34):
                nested = [nested]
            for value in (nested, [0] * 4097, 'x' * 65537):
                with self.subTest(kind=type(value).__name__), self.assertRaisesRegex(ValueError, 'GUIDED_CONST_TOO_COMPLEX'):
                    _decode_schema({'const': value})

        def test_composition_fails_closed_while_parent_union_of_consts_works(self):
            for value in (True, None, [1], {'a': 1}, 1e-5):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'CONST_COMPOSITION'):
                    _decode_schema({'const': value, 'anyOf': [{'type': 'number'}, {'type': 'array'}, {'type': 'object'}, {'type': 'null'}, {'type': 'boolean'}]})
            schema = {'anyOf': [{'const': True}, {'const': {'a': 1}}, {'const': 1e-5}]}
            for text in ('true', '{"a":1}', '1e-05'):
                self.assertTrue(accepts(schema, text))
            for text in ('false', '{"a":2}', '1e-15'):
                self.assertFalse(accepts(schema, text))

        def test_heterogeneous_array_union_keeps_each_branch_stack(self):
            rows = [
                ({'anyOf': [{'type': 'array', 'items': {'type': 'null'}, 'minItems': 2, 'maxItems': 2},
                            {'const': []}]}, ('[]', '[null,null]'), ('[null]', '[null,null,null]')),
                ({'anyOf': [{'type': 'array', 'items': {'const': None}, 'minItems': 1, 'maxItems': 1},
                            {'const': [True]}]}, ('[null]', '[true]'), ('[]', '[nulltrue]', '[null,true]')),
            ]
            for union, valid, invalid in rows:
                for nested in (False, True):
                    schema = ({'type': 'object', 'properties': {'v': union}, 'required': ['v'],
                               'additionalProperties': False} if nested else union)
                    for text in valid + invalid:
                        candidate = '{"v":' + text + '}' if nested else text
                        with self.subTest(schema=schema, text=candidate):
                            self.assertEqual(accepts(schema, candidate), text in valid)

        def test_two_finite_array_branches_keep_shared_prefixes(self):
            for values, valid, invalid in [
                (([], [1]), ('[]', '[1]'), ('[2]', '[1,2]')),
                (([1], [1, 2]), ('[1]', '[1,2]'), ('[]', '[1,3]', '[1,2,3]')),
            ]:
                schema = {'anyOf': [{'const': value} for value in values]}
                for text in valid + invalid:
                    with self.subTest(values=values, text=text):
                        self.assertEqual(accepts(schema, text), text in valid)

        def test_registered_vector_const_survives_transport_object_unions(self):
            from sat_sim.llm_backends import taskspec_draft_response_schema
            cid, field, value = 'orbit_environment.leo_simple.v1', 'sun_vector_n', [.1, .2, .3]
            schema = taskspec_draft_response_schema(cid, parameter_assignments={field: value})
            legacy = {'schema_version': '0.1.0', 'task_id': 't', 'task_type': 'orbit_environment',
                      'capability_id': cid, 'target': {}, 'simulation': {}, 'outputs': {},
                      'parameters': {field: value}}
            canonical = {'schema_version': '1.0.0', 'task': {}, 'simulation': {}, 'outputs': {},
                         'model': {'capability_id': cid}, 'parameters': {'values': {field: value}}}
            config = {'schema_version': '1.0.0', 'task': {}, 'simulation': {}, 'outputs': {},
                      'model': {'capability_id': cid, 'config': {field: value}},
                      'parameters': {'values': {}}}
            for kind, draft in (('legacy', legacy), ('values', canonical), ('config', config)):
                for wrapper in (None, 'task_spec'):
                    for change in ('valid', 'wrong', 'missing'):
                        candidate = copy.deepcopy(draft)
                        carrier = (candidate['parameters'] if kind == 'legacy' else
                                   candidate['parameters']['values'] if kind == 'values' else
                                   candidate['model']['config'])
                        if change == 'wrong':
                            carrier[field] = [.1, .2, .4]
                        elif change == 'missing':
                            del carrier[field]
                        payload = {wrapper: candidate} if wrapper else candidate
                        with self.subTest(kind=kind, wrapper=wrapper, change=change):
                            self.assertEqual(Draft202012Validator(schema).is_valid(payload), change == 'valid')
                            self.assertEqual(accepts(schema, literal(payload)), change == 'valid')

        def test_local_definition_const_and_marker_collision_are_safe(self):
            schema = {'$defs': {'v': {'const': [True, None]}}, '$ref': '#/$defs/v'}
            self.assertTrue(accepts(schema, '[true,null]'))
            self.assertFalse(accepts(schema, '[false,null]'))
            schema = {'type': 'object', 'properties': {'v': {'const': True}, 's': {'const': '__sat_finite_json_const_0__'}}, 'required': ['v', 's']}
            self.assertTrue(accepts(schema, '{"v":true,"s":"__sat_finite_json_const_0__"}'))
            self.assertFalse(accepts(schema, '{"v":"__sat_finite_json_const_0__","s":true}'))

        def test_unconstrained_boolean_enum_remains_explicitly_unsupported(self):
            with self.assertRaisesRegex(ValueError, 'UNSUPPORTED_GUIDED_BOOLEAN_ENUM'):
                response_schema(fmt({'type': 'boolean', 'enum': [True]}))

        def test_actual_local_tokenizer_honors_finite_prefix_and_eos(self):
            import torch
            from transformers import AutoTokenizer
            self.assertFalse(torch.cuda.is_initialized())
            manifest = json.loads((workspace / '.models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json').read_text(encoding='utf-8'))
            tokenizer = AutoTokenizer.from_pretrained(manifest['snapshot'], local_files_only=True, trust_remote_code=False)
            schema = {'type': 'object', 'properties': {'v': {'const': [True, None, {'n': 1e-5, 's': 'a"\\\n'}]}}, 'required': ['v'], 'additionalProperties': False}
            text = literal({'v': schema['properties']['v']['const']})
            class Tokens(list):
                def tolist(self):
                    return list(self)
            def path(candidate):
                hook = prefix_constraint(tokenizer, schema, 256)
                sent = Tokens(tokenizer.encode('Return JSON.', add_special_tokens=False))
                complete_tokens = tokenizer.encode(candidate, add_special_tokens=False)
                for i, token in enumerate(complete_tokens):
                    choices = hook(0, sent)
                    self.assertNotIn(tokenizer.eos_token_id, choices)
                    if token not in choices:
                        return False
                    sent.append(token)
                return tokenizer.eos_token_id in hook(0, sent)
            self.assertTrue(path(text))
            self.assertFalse(path(text.replace('true', 'false', 1)))
            self.assertFalse(path(text.replace('1e-05', '1e-15', 1)))
            schema = {'anyOf': [{'type': 'array', 'items': {'type': 'null'}, 'minItems': 2, 'maxItems': 2},
                                {'const': []}]}
            self.assertTrue(path('[]'))
            self.assertTrue(path('[null,null]'))
            self.assertFalse(path('[null]'))
            self.assertFalse(torch.cuda.is_initialized())

    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(FiniteConstTests))
    import torch
    after = {str(path): digest(path) for path in guarded}
    report = {'status': 'PASS_CPU_CONST_GRAMMAR_ONLY' if result.wasSuccessful() else 'FAIL',
        'tests_run': result.testsRun, 'failures': len(result.failures), 'errors': len(result.errors), 'skipped': len(result.skipped),
        'test_log': stream.getvalue(), 'python': sys.executable,
        'versions': {name: importlib.metadata.version(name) for name in ('lm-format-enforcer', 'transformers', 'torch', 'jsonschema')},
        'sources_before': before, 'sources_after': after, 'source_bytes_unchanged': before == after,
        'audit': audit_counts, 'cuda_initialized': torch.cuda.is_initialized(),
        'worker_sha256': digest(Path(__file__).resolve()),
        'scope': 'CPU actual LMFE and local tokenizer only; no model construction, weights, HTTP/native runs, service control or environment installation.'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.report.exists():
        raise RuntimeError('Do not overwrite previous evidence')
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({key: report[key] for key in ('status', 'tests_run', 'failures', 'errors', 'skipped', 'source_bytes_unchanged', 'cuda_initialized')}))
    if not result.wasSuccessful():
        print(stream.getvalue())
    return 0 if result.wasSuccessful() and before == after and not report['cuda_initialized'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
