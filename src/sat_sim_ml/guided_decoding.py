"""Token-level JSON constraints; never supplies or repairs a model answer."""
from __future__ import annotations

import copy
from dataclasses import replace
import hashlib
import json


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
    return schema


def constrained_json_parser(schema,max_tokens):
    from lmformatenforcer import JsonSchemaParser, CharacterLevelParserConfig
    # The library's default 20-element heuristic must not truncate valid task
    # arrays. The existing token budget already bounds their possible length.
    config = CharacterLevelParserConfig(max_json_array_length=max_tokens)
    schema = copy.deepcopy(schema)
    def equivalent_empty_arrays(value):
        if isinstance(value, dict):
            if value.get('type') == 'array' and value.get('maxItems') == 0:
                # No item can exist, so its type is semantically irrelevant.
                # Avoid LMFE forking an unconstrained item into seven identical
                # empty-list branches, repeatedly multiplying parser work.
                value['items'] = {'type':'null'}
            for child in value.values():
                equivalent_empty_arrays(child)
        elif isinstance(value, list):
            for child in value:
                equivalent_empty_arrays(child)
    equivalent_empty_arrays(schema)
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
