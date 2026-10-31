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


def _finite_json_literal(value):
    """Serialize a bounded finite JSON value, without coercing Python objects."""
    remaining = 4096
    active = set()

    def visit(item, depth):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 32:
            raise ValueError('GUIDED_CONST_TOO_COMPLEX')
        kind = type(item)
        if item is None or kind in (bool, str, int):
            return
        if kind is float:
            if not math.isfinite(item):
                raise ValueError('NONFINITE_GUIDED_NUMERIC_CONST')
            return
        if kind not in (list, dict):
            raise ValueError('UNSUPPORTED_GUIDED_CONST_TYPE:' + kind.__name__)
        if id(item) in active:
            raise ValueError('GUIDED_CONST_CYCLE')
        active.add(id(item))
        if kind is dict and any(type(key) is not str for key in item):
            raise ValueError('GUIDED_CONST_OBJECT_KEYS_MUST_BE_STRINGS')
        for child in (item.values() if kind is dict else item):
            visit(child, depth + 1)
        active.remove(id(item))

    visit(value, 0)
    try:
        text = json.dumps(value, ensure_ascii=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('UNSUPPORTED_GUIDED_CONST_LITERAL') from exc
    if len(text) > 65536:
        raise ValueError('GUIDED_CONST_TOO_COMPLEX')
    return text


def _decode_schema(schema, *, literal_parsers=None):
    """Build a value-exact grammar without mutating the request contract.

    LMFE 0.11.3 sends scalar const values to a string state without converting
    numbers to text (upstream issue 183). Numeric enum already has the correct
    literal path. Other siblings remain, and contradictory const branches are
    rejected conservatively rather than dropping restrictions. Finite compound,
    boolean and null constants use an exact JSON-prefix parser installed by the
    local adapter, not LMFE's unsupported scalar-const path. This does not solve
    arbitrary schema compositions; serialization selects one valid spelling.
    """
    from jsonschema import Draft202012Validator
    from referencing import Registry
    result = copy.deepcopy(schema)
    if schema is None:
        return result
    validator = Draft202012Validator(schema, registry=Registry())
    # A marker is only an internal LMFE construction token. It is replaced by
    # the local parser before any token choices are exposed, and cannot collide
    # with an original schema string (including property names and const data).
    nodes = list(_schema_nodes(result))
    for node in nodes:
        if 'const' in node:
            _finite_json_literal(node['const'])
    strings = set()
    def collect_strings(item):
        if isinstance(item, str):
            strings.add(item)
        elif isinstance(item, dict):
            for key, child in item.items():
                strings.add(key)
                collect_strings(child)
        elif isinstance(item, list):
            for child in item:
                collect_strings(child)
    collect_strings(schema)
    markers = literal_parsers if literal_parsers is not None else {}
    for node in nodes:
        # bool is an int subclass in Python; upstream enum spells it True/False.
        if 'const' not in node and any(type(value) is bool for value in node.get('enum', [])):
            raise ValueError('UNSUPPORTED_GUIDED_BOOLEAN_ENUM')
        if 'const' not in node:
            continue
        value = node['const']
        literal = _finite_json_literal(value)
        if any(keyword in node for keyword in ('anyOf', 'allOf', 'oneOf', '$ref')):
            # LMFE prioritizes these over enum; retaining them could bypass
            # the singleton restriction. Do not claim generic intersection support.
            reason = 'NUMERIC_' if type(value) in (int, float) else ''
            raise ValueError('UNSUPPORTED_GUIDED_' + reason + 'CONST_COMPOSITION')
        try:
            compatible = validator.evolve(schema=node).is_valid(value)
        except Exception as exc:
            raise ValueError('GUIDED_CONST_CONSTRAINT_UNRESOLVED') from exc
        if not compatible:
            # An impossible branch may live inside an otherwise viable anyOf.
            # We reject that input explicitly; do not silently prune or weaken it.
            raise ValueError('GUIDED_CONST_CONSTRAINT_CONTRADICTION')
        if type(value) is str:
            continue  # Existing LMFE string constants already have a safe path.
        if type(value) not in (int, float):
            index = len(markers)
            marker = '__sat_finite_json_const_' + str(index) + '__'
            while marker in strings or marker in markers:
                index += 1
                marker = '__sat_finite_json_const_' + str(index) + '__'
            markers[marker] = literal
            # The original node has just been validated against the constant,
            # so an exact prefix grammar intersects every original restriction.
            node.clear()
            node.update({'type': 'string', 'enum': [marker]})
            continue
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
    literal_parsers = {}
    schema = _decode_schema(schema, literal_parsers=literal_parsers)
    for value in _schema_nodes(schema):
        if value.get('type') == 'array' and value.get('maxItems') == 0:
            # No item can exist, so its type is semantically irrelevant.
            # Avoid LMFE forking an unconstrained item into seven identical
            # empty-list branches, repeatedly multiplying parser work.
            value['items'] = {'type':'null'}
    from .bounded_numeric_parser import BoundedJsonParser
    return BoundedJsonParser(JsonSchemaParser(schema,config=config), literal_parsers)


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
