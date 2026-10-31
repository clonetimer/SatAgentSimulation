"""Literal user parameter contracts, shared by prompt, decoder and hard guard.

Only assignments and explicitly introduced configuration/parameter JSON are
requirements. Advisory examples, arbitrary prose and event payloads are not.
This module validates requirements; it never edits a model answer.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Mapping


class ExplicitParameterError(ValueError):
    """An explicit user parameter requirement is ambiguous or invalid."""


def same_literal(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return math.isfinite(left) and math.isfinite(right) and left == right
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(same_literal(left[k], right[k]) for k in left)
    if isinstance(left, list):
        return len(left) == len(right) and all(same_literal(a, b) for a, b in zip(left, right))
    return left == right


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ExplicitParameterError(f"Duplicate JSON configuration key: {key}")
        result[key] = value
    return result


def _nonfinite(value):
    raise ExplicitParameterError(f"Non-finite JSON literal is not supported: {value}")


def parameter_schema(descriptor: Mapping[str, Any]) -> dict[str, Any]:
    # Reuse the same registry type translation as the transport schema.
    from .llm_backends import _registry_parameter_type_schema
    import copy
    schema = _registry_parameter_type_schema(str(descriptor.get('type')))
    for source, target in [('enum', 'enum'), ('items', 'items'), ('min', 'minimum'), ('max', 'maximum')]:
        if source in descriptor:
            schema[target] = copy.deepcopy(descriptor[source])
    return schema


def _finite(value):
    if isinstance(value, float) and not math.isfinite(value):
        return False
    if isinstance(value, list):
        return all(_finite(v) for v in value)
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    return True


_NON_PARAMETER_ASSIGNMENTS = {
    'capability_id', 'task_id', 'task_type', 'schema_version', 'backend', 'duration',
    'duration_s', 'sample_s', 'step_s', 'seed', 'target_mode', 'mode', 'subsystem',
    'fault', 'faults', 'degradations', 'constraints', 'events', 'plots', 'qoi',
    'output_root', 'trace_format', 'include_endpoint', 'method', 'rtol', 'atol',
    'configuration', 'config', 'parameters',
}
_CONFIG_KEYS = {'configuration', 'config', 'parameters'}
_ROOT_KEYS = _NON_PARAMETER_ASSIGNMENTS | _CONFIG_KEYS | {
    'simulation', 'outputs', 'model', 'target', 'mission', 'solver',
    'solver.step_s', 'solver.method', 'solver.rtol', 'solver.atol',
}
_INTRO = re.compile(r'(?<![\w.])(?:(?:required|requested|explicit)\s+)?(?:configuration|config|parameters|配置|参数)\s*[:：=]\s*', re.I)
_EXAMPLE = re.compile(r'\b(?:examples?|illustrations?|e\.g\.)\b|示例|例如', re.I)
_FENCE = re.compile(r'```(?:json)?[ \t]*\r?\n(?P<body>[\s\S]*?)\r?\n[ \t]*```', re.I)


def _example_prefix(prefix: str) -> bool:
    """An example label applies to its current sentence, not later requests."""
    matches = list(_EXAMPLE.finditer(prefix))
    return bool(matches) and not re.search(r'[。！？!?;；]|\.(?=\s|$)', prefix[matches[-1].end():])


def explicit_parameter_assignments(text: str, declared: Mapping[str, Any]) -> dict[str, Any]:
    """Parse registered key=literal and requested config JSON, never defaults.

    JSON must be explicitly introduced, or be an entire/fenced configuration
    document. Duplicate/conflicting/unknown/ill-typed/out-of-range values fail.
    Latest refinement instructions own the requirements, as before.
    """
    from jsonschema import Draft202012Validator

    latest = text.rsplit('本轮补充指令：', 1)[-1]
    decoder = json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=_nonfinite)
    expected: dict[str, Any] = {}
    spans = []

    def add(name, value):
        if name not in declared:
            raise ExplicitParameterError(f"Unknown explicit parameter: {name}")
        schema = parameter_schema(declared[name])
        # JSON Schema regards 1.0 as integer; explicit integer fields require an
        # integer token and never accept bool (a Python int subclass).
        kind = str(declared[name].get('type'))
        if not _finite(value) or (kind == 'integer' and type(value) is not int):
            raise ExplicitParameterError(f"Invalid explicit parameter type/value: {name}={value!r}")
        if list(Draft202012Validator(schema).iter_errors(value)):
            raise ExplicitParameterError(f"Explicit parameter violates registry type/bounds/enum: {name}={value!r}")
        if name in expected and not same_literal(expected[name], value):
            raise ExplicitParameterError(f"Conflicting explicit parameter values: {name}")
        expected[name] = value

    def consume(obj, *, parameter_container=False):
        if not isinstance(obj, dict):
            raise ExplicitParameterError("Explicit configuration/parameters must be a JSON object")
        if parameter_container:
            # Canonical parameter envelopes are distinct from a flat parameter
            # object; do not interpret event parameters as initial settings.
            if 'values' in obj:
                if set(obj) - {'values', 'profile', 'overrides'}:
                    raise ExplicitParameterError("Unknown canonical parameter envelope key")
                if obj.get('overrides'):
                    raise ExplicitParameterError("Explicit parameter overrides must be expressed in values, not an ambiguous override list")
                consume(obj['values'], parameter_container=True)
            else:
                for name, value in obj.items():
                    add(name, value)
            return
        for name, value in obj.items():
            if name in declared:
                add(name, value)
            elif name == 'parameters':
                consume(value, parameter_container=True)
            elif name in {'configuration', 'config'}:
                consume(value)
            elif name == 'model':
                if not isinstance(value, dict):
                    raise ExplicitParameterError("Explicit model configuration must be an object")
                if 'config' in value:
                    consume(value['config'], parameter_container=True)
            elif name not in _ROOT_KEYS:
                raise ExplicitParameterError(f"Unknown explicit configuration key: {name}")

    def parse_at(start, *, parameter_container=False):
        try:
            obj, end = decoder.raw_decode(latest[start:])
        except (ValueError, TypeError) as exc:
            raise ExplicitParameterError(f"Invalid explicit configuration JSON: {exc}") from exc
        consume(obj, parameter_container=parameter_container)
        spans.append((start, start + end))

    def parse_fenced(match, *, parameter_container=False):
        try:
            # A settings document is the whole fenced body. Reject trailing
            # objects/prose instead of accepting only its first JSON object.
            obj = decoder.decode(match.group('body').strip())
        except (ValueError, TypeError) as exc:
            raise ExplicitParameterError(f"Invalid explicit configuration JSON: {exc}") from exc
        consume(obj, parameter_container=parameter_container)
        spans.append(match.span())

    for match in _INTRO.finditer(latest):
        if any(start <= match.start() < end for start, end in spans):
            continue
        prefix = latest[max(0, latest.rfind('\n\n', 0, match.start())):match.start()]
        if _example_prefix(prefix):
            continue
        start = match.end()
        parameter_container = bool(re.search(r'(?:parameters|参数)\s*[:：=]\s*$', match.group(0), re.I))
        if start < len(latest) and latest[start] == '{':
            parse_at(start, parameter_container=parameter_container)
        elif latest.startswith('```', start) and (fenced := _FENCE.match(latest, start)):
            parse_fenced(fenced, parameter_container=parameter_container)
        else:
            raise ExplicitParameterError("Explicit configuration/parameters must be a JSON object")

    stripped = latest.strip()
    if stripped.startswith('{') and not spans:
        parse_at(len(latest) - len(latest.lstrip()))
    for match in _FENCE.finditer(latest):
        if any(start <= match.start() < end for start, end in spans):
            continue
        prefix = latest[max(0, latest.rfind('\n\n', 0, match.start())):match.start()]
        if _example_prefix(prefix):
            continue
        # Fenced arbitrary examples are advisory. A parameter/configuration
        # envelope explicitly identifies a machine-readable settings document.
        if re.match(r'\s*\{\s*"(?:parameters|configuration|config|model)"\s*:', match.group('body')):
            parse_fenced(match)

    for match in re.finditer(r'(?<![\w.])([A-Za-z_][A-Za-z0-9_]*)\s*=\s*', latest):
        if any(start <= match.start() < end for start, end in spans):
            continue
        prefix = re.split(r'[\n。;；]', latest[:match.start()])[-1]
        if _example_prefix(prefix):
            continue
        name = match.group(1)
        if name in _NON_PARAMETER_ASSIGNMENTS and name not in declared:
            continue
        if name not in declared:
            raise ExplicitParameterError(f"Unknown explicit parameter: {name}")
        remainder = latest[match.end():].lstrip()
        try:
            literal, end = decoder.raw_decode(remainder)
            if end < len(remainder) and not re.match(r'[\s,，;；。]', remainder[end]):
                raise ValueError('not a complete literal')
        except ExplicitParameterError:
            raise
        except (ValueError, TypeError):
            token = re.match(r'[^\s,，;；。]+', remainder)
            literal = token.group(0) if token else ''
            # Preserve the existing bare-string assignment interface, while
            # treating invalid numeric/non-finite tokens as explicit errors.
            if re.fullmatch(r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?', literal):
                literal = float(literal)
        add(name, literal)
    return expected


def constrain_parameter_schema(schema: Mapping[str, Any], value: Any) -> dict[str, Any]:
    """Equivalent literal constraint compatible with the existing LMFE adapter.

    Registry scalar/array type unions are resolved by the literal type. The
    guided adapter must enforce compound constants with its exact JSON literal
    parser; tuple/prefixItems support is never assumed.
    """
    import copy
    from jsonschema import Draft202012Validator
    out = copy.deepcopy(dict(schema))
    if list(Draft202012Validator(out).iter_errors(value)) or not _finite(value):
        raise ExplicitParameterError("Literal contradicts its original registry schema")
    if 'anyOf' in out:
        siblings = {key: val for key, val in out.items() if key != 'anyOf'}
        branches = [branch for branch in out['anyOf'] if Draft202012Validator(branch).is_valid(value)]
        if len(branches) != 1:
            raise ExplicitParameterError("Ambiguous explicit parameter type union")
        branch = copy.deepcopy(branches[0])
        for key, val in siblings.items():
            if key in branch and branch[key] != val:
                raise ExplicitParameterError("Conflicting registry union constraints")
            branch[key] = val
        return constrain_parameter_schema(branch, value)
    out['const'] = value
    return out
