"""Token-level JSON constraints; never supplies or repairs a model answer."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json
import math


def _schema_nodes(schema):
    """Visit schema positions, not instance data in defaults/const/examples."""
    if not isinstance(schema, dict):
        return
    yield schema
    for keyword in ('properties', 'patternProperties', '$defs', 'definitions',
                    'dependentSchemas', 'dependencies'):
        children = schema.get(keyword, {})
        if isinstance(children, dict):
            for child in children.values():
                yield from _schema_nodes(child)
    for keyword in ('items', 'additionalItems', 'additionalProperties', 'contains',
                    'unevaluatedItems', 'unevaluatedProperties', 'propertyNames',
                    'not', 'if', 'then', 'else', 'contentSchema'):
        yield from _schema_nodes(schema.get(keyword))
    for keyword in ('allOf', 'anyOf', 'oneOf', 'prefixItems'):
        for child in schema.get(keyword, []):
            yield from _schema_nodes(child)


def _decode_schema(schema):
    """Make an equivalent numeric-const grammar without mutating its contract.

    LMFE 0.11.3 sends scalar const values to a string state without converting
    numbers to text (upstream issue 183). Numeric enum already has the correct
    literal path. Other siblings remain, and contradictory const branches are
    rejected conservatively rather than dropping restrictions. This is not a
    general JSON Schema solver or support for arbitrary compound constants.
    """
    from jsonschema import Draft202012Validator
    from referencing import Registry
    result = copy.deepcopy(schema)
    if schema is None:
        return result
    validator = Draft202012Validator(schema, registry=Registry())
    for node in _schema_nodes(result):
        # bool is an int subclass in Python; upstream enum spells it True/False.
        if any(type(value) is bool for value in node.get('enum', [])):
            raise ValueError('UNSUPPORTED_GUIDED_BOOLEAN_ENUM')
        if 'const' not in node:
            continue
        value = node['const']
        if type(value) is str:
            continue
        if type(value) not in (int, float):
            kind = {bool:'boolean', type(None):'null', list:'array', dict:'object'}.get(type(value), type(value).__name__)
            raise ValueError('UNSUPPORTED_GUIDED_CONST_TYPE:' + kind)
        if type(value) is float and not math.isfinite(value):
            raise ValueError('NONFINITE_GUIDED_NUMERIC_CONST')
        if any(keyword in node for keyword in ('anyOf', 'allOf', 'oneOf', '$ref')):
            # LMFE prioritizes these over enum; retaining them could bypass
            # the singleton restriction. Do not claim generic intersection support.
            raise ValueError('UNSUPPORTED_GUIDED_NUMERIC_CONST_COMPOSITION')
        try:
            compatible = validator.evolve(schema=node).is_valid(value)
        except Exception as exc:
            raise ValueError('GUIDED_CONST_CONSTRAINT_UNRESOLVED') from exc
        if not compatible:
            # An impossible branch may live inside an otherwise viable anyOf.
            # We reject that input explicitly; do not silently prune or weaken it.
            raise ValueError('GUIDED_CONST_CONSTRAINT_CONTRADICTION')
        node.pop('const')
        # This is also the exact intersection with an existing enum sibling,
        # whose compatibility with the constant was checked above.
        node['enum'] = [value]
    return result


def response_schema(response_format):
    fmt = response_format or {}
    kind = fmt.get('type','text')
    if kind == 'text':
        return None
    if kind == 'json_object':
        return {'type':'object'}
    if kind != 'json_schema':
        raise ValueError('UNSUPPORTED_RESPONSE_FORMAT')
    schema = copy.deepcopy(fmt['json_schema']['schema'])
    from jsonschema import Draft202012Validator
    def local_refs(value):
        if isinstance(value,dict):
            if '$ref' in value and not str(value['$ref']).startswith('#'):
                raise ValueError('REMOTE_SCHEMA_REFERENCES_FORBIDDEN')
            for child in value.values():
                local_refs(child)
        elif isinstance(value,list):
            for child in value:
                local_refs(child)
    local_refs(schema)
    Draft202012Validator.check_schema(schema)
    _decode_schema(schema)  # Fail unsupported constants before tokenizer/model work.
    return schema


def constrained_json_parser(schema,max_tokens):
    from lmformatenforcer import JsonSchemaParser, CharacterLevelParserConfig
    # The library's default 20-element heuristic must not truncate valid task
    # arrays. The existing token budget already bounds their possible length.
    config = CharacterLevelParserConfig(max_json_array_length=max_tokens)
    schema = _decode_schema(schema)
    for value in _schema_nodes(schema):
        if value.get('type') == 'array' and value.get('maxItems') == 0:
            # No item can exist, so its type is semantically irrelevant.
            # Avoid LMFE forking an unconstrained item into seven identical
            # empty-list branches, repeatedly multiplying parser work.
            value['items'] = {'type':'null'}
    from .bounded_numeric_parser import BoundedJsonParser
    return BoundedJsonParser(JsonSchemaParser(schema,config=config))


def prefix_constraint(tokenizer,schema,max_tokens):
    from lmformatenforcer.integrations.transformers import build_transformers_prefix_allowed_tokens_fn
    hook = build_transformers_prefix_allowed_tokens_fn(tokenizer,constrained_json_parser(schema,max_tokens))
    # TokenEnforcer installs its own alphabet/config, replacing the parser's
    # constructor config. Preserve that alphabet and restore our token budget.
    parser = hook.token_enforcer.root_parser
    parser.config = replace(parser.config,max_json_array_length=max_tokens)
    prompt_length = None

    def allowed(batch_id,sent):
        nonlocal prompt_length
        if prompt_length is None:
            prompt_length = len(sent)
        tokens = hook(batch_id,sent)
        if tokenizer.eos_token_id in tokens:
            # LMFE can force EOS after an internal parser error. Never present
            # a truncated completion as a successful structured generation.
            try:
                json.loads(tokenizer.decode(sent[prompt_length:],skip_special_tokens=True))
            except (ValueError,TypeError) as exc:
                raise ValueError('GUIDED_DECODER_PREMATURE_END') from exc
        return tokens

    return allowed


def decoding_evidence(schema):
    return {'enabled':schema is not None,
            'method':'lm-format-enforcer' if schema is not None else 'unconstrained',
            'schema_sha256':hashlib.sha256(json.dumps(schema,sort_keys=True).encode()).hexdigest()
                if schema is not None else None,
            'answer_substitution':False,'semantic_validation_required':True,
            'numeric_bounds_at_decode':schema is not None,
            'empty_arrays_at_decode':schema is not None,
            'invalid_numeric_mantissa_digit_budget':17 if schema is not None else None,
            'canonical_exponent_range':[-324,308] if schema is not None else None}
