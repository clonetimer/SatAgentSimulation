"""Pluggable LLM backends for capability-aware TaskSpec generation.

The simulation toolchain treats an LLM as an untrusted draft generator.  Backends
in this module return raw model text only; parsing, validation, repair,
compilation, script generation, and optional execution remain local trusted
steps in :mod:`sat_sim.capability_agent`.

The dependency-free command backend is the recommended integration seam for
local LLMs, hosted model CLIs, LangChain/LangGraph wrappers, or enterprise Agent
runtimes: the backend sends the prompt on stdin and captures stdout.

Optional direct HTTPS backends are provided for deployments that want to call
OpenAI Responses API or DeepSeek Chat Completions without making vendor SDKs hard
dependencies.  Networked backends are opt-in and are not exercised by tests
against live services.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

import hashlib
import copy
import json
import os
import shlex
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import yaml

OPENAI_TASKSPEC_SYSTEM_PROMPT = (
    'Generate one sat-sim TaskSpec only. Capability Registry data in the prompt is authoritative. '
    'Do not invent capabilities, effects, Python code, imports, fidelity or claims.'
)


class LLMBackendError(RuntimeError):
    """Raised when an LLM backend cannot produce a draft."""


@dataclass(frozen=True)
class LLMCallConfig:
    """Configuration for one external model call."""

    backend: str = "template"
    command: str | None = None
    model: str | None = None
    base_url: str | None = None
    api_key_env: str = "OPENAI_API_KEY"
    timeout_s: float = 60.0
    temperature: float | None = None
    seed: int | None = None
    max_output_tokens: int | None = None
    structured_output: str = "text"
    response_schema: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # Never serialize API key values; only serialize the env var name.
        payload["api_key"] = None
        return payload


@dataclass(frozen=True)
class LLMCallResult:
    """Raw output and metadata from an LLM backend."""

    ok: bool
    backend: str
    raw_text: str
    prompt_chars: int
    metadata: dict[str, Any]
    error: str | None = None

    @staticmethod
    def _sha256_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def invocation_evidence(self) -> dict[str, Any]:
        """Return privacy-preserving proof that one model response was received.

        The evidence intentionally stores hashes and provider metadata rather than
        treating a backend step name as proof of model use.  The raw response is
        preserved separately in ``model_call.json`` for operator-controlled
        debugging, while downstream claim logic consumes this compact evidence.
        """

        raw = str(self.raw_text or "")
        structured_hash: str | None = None
        structured_kind: str | None = None
        if raw.strip():
            try:
                parsed = json.loads(raw)
                structured_kind = "json"
            except Exception:
                try:
                    parsed = yaml.safe_load(raw)
                    structured_kind = "yaml"
                except Exception:
                    parsed = None
            if isinstance(parsed, (Mapping, list)):
                canonical = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                structured_hash = self._sha256_text(canonical)
        model_id = self.metadata.get("model") or self.metadata.get("model_id")
        provider_response_id = self.metadata.get("response_id") or self.metadata.get("id")
        response_sha256 = self._sha256_text(raw) if raw else None
        request_seed = "|".join(
            [str(self.backend), str(model_id or ""), str(provider_response_id or ""), response_sha256 or ""]
        )
        request_id = str(provider_response_id or f"local-{self._sha256_text(request_seed)[:24]}")
        verified = bool(self.ok and raw.strip() and response_sha256 and request_id)
        return {
            "schema_version": "sat-sim.model-invocation-evidence.v1",
            "verified": verified,
            "backend": self.backend,
            "model_id": model_id,
            "request_id": request_id,
            "provider_response_id": provider_response_id,
            "prompt_sha256": self.metadata.get("prompt_sha256"),
            "response_sha256": response_sha256,
            "structured_output_sha256": structured_hash,
            "structured_output_kind": structured_kind,
            "usage": self.metadata.get("usage"),
            "structured_generation": self.metadata.get("structured_generation"),
            "latency_ms": self.metadata.get("latency_ms"),
            "endpoint": self.metadata.get("endpoint") or self.metadata.get("base_url"),
            "error": self.error,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "raw_text": self.raw_text,
            "prompt_chars": self.prompt_chars,
            "metadata": self.metadata,
            "invocation_evidence": self.invocation_evidence(),
            "error": self.error,
        }


def _call_metadata(prompt: str, metadata: Mapping[str, Any] | None = None, *, started: float | None = None) -> dict[str, Any]:
    payload = dict(metadata or {})
    payload.setdefault("prompt_sha256", hashlib.sha256(prompt.encode("utf-8")).hexdigest())
    if started is not None:
        payload.setdefault("latency_ms", round((time.perf_counter() - started) * 1000.0, 2))
    return payload


class TextLLMBackend(Protocol):
    """Protocol for prompt-in, text-out model integrations."""

    name: str

    def generate_text(self, prompt: str) -> LLMCallResult:
        """Generate raw model text from a prompt."""


class CommandLLMBackend:
    """Invoke an external command and pass the prompt on stdin.

    The command should write a YAML/JSON TaskSpec, optionally inside a Markdown
    code fence, to stdout.  This makes the backend independent of any particular
    Agent framework or vendor SDK.

    If the command string contains ``{prompt_file}``, the prompt is also written
    to a temporary file and the placeholder is replaced with that path.  The
    prompt is still sent on stdin, so simple commands do not need to use the
    placeholder.
    """

    name = "command"

    def __init__(self, command: str, *, timeout_s: float = 60.0) -> None:
        if not command or not command.strip():
            raise LLMBackendError("command backend requires a non-empty command")
        self.command = command
        self.timeout_s = float(timeout_s)

    def _argv(self, prompt: str) -> tuple[list[str], str | None]:
        prompt_file: str | None = None
        command = self.command
        if "{prompt_file}" in command:
            fd, prompt_file = tempfile.mkstemp(prefix="sat_sim_prompt_", suffix=".md")
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(prompt)
            command = command.replace("{prompt_file}", shlex.quote(prompt_file))
        return shlex.split(command), prompt_file

    def generate_text(self, prompt: str) -> LLMCallResult:
        prompt_file: str | None = None
        started = time.perf_counter()
        try:
            argv, prompt_file = self._argv(prompt)
            proc = subprocess.run(
                argv,
                input=prompt,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout_s,
                check=False,
            )
            metadata = _call_metadata(prompt, {
                "command": self.command,
                "argv": argv,
                "returncode": proc.returncode,
                "stderr": proc.stderr[-4000:],
                "model": Path(argv[0]).name if argv else "command",
            }, started=started)
            if proc.returncode != 0:
                return LLMCallResult(False, self.name, proc.stdout, len(prompt), metadata, error=f"command exited with {proc.returncode}")
            return LLMCallResult(True, self.name, proc.stdout, len(prompt), metadata)
        except subprocess.TimeoutExpired as exc:
            return LLMCallResult(False, self.name, exc.stdout or "", len(prompt), _call_metadata(prompt, {"command": self.command, "timeout_s": self.timeout_s, "model": "command"}, started=started), error="command timed out")
        except Exception as exc:
            return LLMCallResult(False, self.name, "", len(prompt), _call_metadata(prompt, {"command": self.command, "model": "command"}, started=started), error=str(exc))
        finally:
            if prompt_file:
                try:
                    Path(prompt_file).unlink(missing_ok=True)
                except Exception as exc:
                    record_runtime_diagnostic(
                        code='LLM_PROMPT_TEMPFILE_CLEANUP_FAILED',
                        category=DiagnosticCategory.BEST_EFFORT_CLEANUP,
                        location='src/sat_sim/llm_backends.py:generate_text:01',
                        exception=exc,
                        strict=False,
                    )


class OpenAIResponsesBackend:
    """Minimal dependency-free OpenAI Responses API backend.

    This backend uses ``urllib`` rather than the OpenAI SDK so that sat-sim can
    remain installable in offline simulation environments.  It is optional and
    only runs when the caller selects backend="openai" and provides an API key
    via the configured environment variable.
    """

    name = "openai"

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "https://api.openai.com/v1/responses",
        timeout_s: float = 60.0,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        structured_output: str = "text",
    ) -> None:
        if not model:
            raise LLMBackendError("openai backend requires --model or SAT_SIM_OPENAI_MODEL")
        self.model = model
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.timeout_s = float(timeout_s)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def _extract_output_text(self, payload: Mapping[str, Any]) -> str:
        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text
        chunks: list[str] = []
        output = payload.get("output")
        if isinstance(output, list):
            for item in output:
                if not isinstance(item, Mapping):
                    continue
                content = item.get("content")
                if isinstance(content, list):
                    for part in content:
                        if isinstance(part, Mapping):
                            text = part.get("text")
                            if isinstance(text, str):
                                chunks.append(text)
        return "\n".join(chunks).strip()

    def generate_text(self, prompt: str) -> LLMCallResult:
        started = time.perf_counter()
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            return LLMCallResult(False, self.name, "", len(prompt), _call_metadata(prompt, {"model": self.model, "api_key_env": self.api_key_env}, started=started), error=f"missing API key env var: {self.api_key_env}")
        body: dict[str, Any] = {
            "model": self.model,
            "input": prompt,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.max_output_tokens is not None:
            body["max_output_tokens"] = int(self.max_output_tokens)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base_url,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # nosec B310 - opt-in API backend
                raw = resp.read().decode("utf-8")
            payload = json.loads(raw)
            text = self._extract_output_text(payload)
            metadata = _call_metadata(prompt, {
                "model": self.model,
                "base_url": self.base_url,
                "response_id": payload.get("id"),
                "status": payload.get("status"),
                "usage": payload.get("usage"),
            }, started=started)
            if not text:
                return LLMCallResult(False, self.name, raw, len(prompt), metadata, error="response did not contain output text")
            return LLMCallResult(True, self.name, text, len(prompt), metadata)
        except urllib.error.HTTPError as exc:
            err_text = exc.read().decode("utf-8", errors="replace")
            return LLMCallResult(False, self.name, err_text, len(prompt), _call_metadata(prompt, {"model": self.model, "status": exc.code, "base_url": self.base_url}, started=started), error=f"HTTP {exc.code}")
        except Exception as exc:
            return LLMCallResult(False, self.name, "", len(prompt), _call_metadata(prompt, {"model": self.model, "base_url": self.base_url}, started=started), error=str(exc))


def _registry_parameter_type_schema(kind: str) -> dict[str, Any]:
    """Translate the registry's type notation without leaving untyped fields."""
    if kind == 'bool':
        kind = 'boolean'
    if kind in {'number','integer','string','boolean','array','object','null'}:
        return {'type':kind}
    if kind.startswith('array[') and kind.endswith(']'):
        return {'type':'array','items':_registry_parameter_type_schema(kind[6:-1])}
    vector = {'type':'array','items':{'type':'number'},'minItems':3,'maxItems':3}
    if kind == 'vector3':
        return vector
    if kind == 'vector3_or_number':
        return {'anyOf':[{'type':'number'},vector]}
    if kind == 'number_or_array[number]':
        return {'anyOf':[{'type':'number'},{'type':'array','items':{'type':'number'}}]}
    raise ValueError(f'UNSUPPORTED_REGISTRY_PARAMETER_TYPE:{kind}')


def _event_draft_schemas(contract: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Require parseable event discriminators, without supplying user values.

    Legacy aliases remain accepted at the parser boundary. Generated drafts
    use the selected registry's public effect names; validation stays mandatory.
    """
    legacy_items, canonical_items = {}, {}
    for plural, mode, type_key in (
        ('faults', 'fault', 'fault_type'),
        ('degradations', 'degradation', 'degradation_type'),
        ('constraints', 'constraint', 'constraint_type'),
    ):
        mode_contract = contract.get('modes', {}).get(mode, {})
        effects = mode_contract.get('effects') or mode_contract.get(mode + '_types') or []
        effect = {'type': 'string', 'minLength': 1}
        if effects:
            effect['enum'] = list(dict.fromkeys(str(value) for value in effects))
        common = {name: {'type': 'string'} for name in (
            'target', 'target_path', 'target_type', 'label', 'implementation')}
        common.update({name: {'type': 'number', 'minimum': 0, 'maximum': 1}
                       for name in ('magnitude', 'severity')})
        common.update({name: {'type': 'number', 'minimum': 0}
                       for name in ('onset_time_s', 'start_time_s', 'scale')})
        common['duration_s'] = {'type': 'number', 'minimum': -1}
        common['parameters'] = {'type': 'object', 'properties': {'scenario': copy.deepcopy(effect)}}
        common.update({name: copy.deepcopy(effect) for name in (type_key, 'modifier_type', 'type')})
        common.update({name: {'type': 'string'} for name in (mode + '_id', 'modifier_id')})
        alternatives = []
        for discriminator in (type_key, 'modifier_type', 'type', 'parameters'):
            properties = copy.deepcopy(common)
            if discriminator == 'parameters':
                properties['parameters']['required'] = ['scenario']
            alternatives.append({'type': 'object', 'properties': properties,
                                 'required': [discriminator], 'additionalProperties': False})
        legacy_items[plural] = {'anyOf': alternatives}
        canonical_properties = {
            'id': {'type': 'string', 'minLength': 1},
            'target': {'type': 'string', 'minLength': 1}, 'effect': copy.deepcopy(effect),
            'start_s': {'type': 'number', 'minimum': 0},
            'end_s': {'anyOf': [{'type': 'number', 'exclusiveMinimum': 0}, {'type': 'null'}]},
            'magnitude': {'anyOf': [copy.deepcopy(common['magnitude']), {'type': 'null'}]},
            'scale': {'anyOf': [copy.deepcopy(common['scale']), {'type': 'null'}]},
            'implementation': {'enum': ['auto', 'native', 'proxy']},
            'delivery': {'enum': ['modifier', 'legacy_fault', 'legacy_degradation',
                                  'runtime_parameter', 'runtime_constraint']},
            'target_type': {'type': ['string', 'null']}, 'label': {'type': ['string', 'null']},
            'parameters': {'type': 'object'}, 'event_type': {'const': mode},
        }
        canonical_items[plural] = {'type': 'object', 'properties': canonical_properties,
                                   'required': ['id', 'target', 'effect'], 'additionalProperties': False}
    return legacy_items, canonical_items


def taskspec_draft_response_schema(capability_id: str | None = None, *, forbid_events: bool = False,
                                 constrain_events: bool = False,
                                 single_fault: bool = False,
                                 single_fault_carrier: str = 'auto',
                                 allow_geometry_overrides: bool = True,
                                 allow_per_wheel_overrides: bool = True,
                                 parameter_assignments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Transport shape for the legacy/canonical/wrapped drafts we parse.

    Capability identity may be restored from an unambiguous registry binding
    by the existing normalizer. Full semantic validation remains mandatory
    after parsing; this schema is never evidence of a valid simulation.
    """
    if single_fault_carrier not in {'auto', 'faults', 'modifiers'}:
        raise ValueError('UNSUPPORTED_SINGLE_FAULT_CARRIER')
    legacy = {
        "type": "object",
        "properties": {
            "schema_version": {"type": "string"},
            "task_id": {"type": "string"},
            "task_type": {
                "type": "string",
                "enum": [
                    "component",
                    "subsystem",
                    "orbit_environment",
                    "whole_spacecraft",
                    "campaign",
                    "reference",
                ],
            },
            "description": {"type": "string"},
            "capability_id": {"type": "string"},
            "target": {"type": "object"},
            "simulation": {"type": "object"},
            "spacecraft": {"type": "object"},
            "mission": {"type": "object"},
            "campaign": {"type": "object"},
            "validation": {"type": "object"},
            "assurance": {"type": "object"},
            "parameters": {"type": "object"},
            "orbit_environment": {"type": "object"},
            "faults": {"type": "array"},
            "degradations": {"type": ["array", "object"]},
            "constraints": {"type": ["array", "object"]},
            "modifiers": {"type": "object"},
            "outputs": {"type": "object"},
            "metadata": {"type": "object"},
            "tags": {"type": "array"},
        },
        "required": [
            "schema_version",
            "task_id",
            "task_type",
            "target",
            "simulation",
            "outputs",
        ],
        "additionalProperties": False,
    }
    canonical = {
        "type": "object",
        "properties": {"schema_version": {"type": "string"}, **{
            name: {"type": "object"} for name in (
                "task", "simulation", "mission", "parameters", "events", "outputs",
                "assurance", "model", "provenance", "metadata")}},
        "required": ["schema_version", "task", "simulation", "outputs"],
        "additionalProperties": False,
    }
    if capability_id is not None:
        from .capability_registry import get_capability
        contract = get_capability(capability_id).data
        parameter_properties = {}
        for name, descriptor in contract.get('parameters', {}).items():
            if not allow_per_wheel_overrides and name.startswith('rw_') and name.endswith(('_0','_1','_2','_3')) and not name.startswith(('rw_axis_b_', 'rw_mount_b_')):
                continue
            if not allow_geometry_overrides and (name.startswith(('rw_axis_b_', 'rw_mount_b_'))
                    or name in {'rw_geometry_mode', 'rw_mount_radius_m'}):
                continue
            kind = descriptor.get('type')
            item = _registry_parameter_type_schema(str(kind))
            for source,target in [('enum','enum'),('items','items'),('min','minimum'),('max','maximum')]:
                if source in descriptor:
                    item[target] = copy.deepcopy(descriptor[source])
            parameter_properties[name] = item
        if parameter_assignments:
            from .explicit_parameters import ExplicitParameterError, constrain_parameter_schema
            for name, value in parameter_assignments.items():
                if name not in parameter_properties:
                    raise ExplicitParameterError(f'Explicit parameter is unavailable in this decoder contract: {name}')
                parameter_properties[name] = constrain_parameter_schema(parameter_properties[name], value)
        parameters = {'type':'object','properties':parameter_properties,'additionalProperties':False}
        legacy['properties']['schema_version'] = {'const':'0.1.0'}
        legacy['properties']['capability_id'] = {'const':capability_id}
        legacy['properties']['parameters'] = parameters
        legacy['required'].append('capability_id')
        canonical['properties']['schema_version'] = {'const':'1.0.0'}
        canonical['properties']['parameters'] = {'type':'object','properties':{
            'profile':{'type':'string'},'values':copy.deepcopy(parameters),'overrides':{'type':'array'}},
            'additionalProperties':False}
        canonical['properties']['model'] = {'type':'object','properties':{
            'capability_id':{'const':capability_id},'config':copy.deepcopy(parameters),
            **{name:{'type':'object'} for name in ('target','spacecraft','orbit_environment',
                'legacy_degradations','campaign','simulation_extra','validation')}},
            'required':['capability_id'],'additionalProperties':False}
        canonical['required'].append('model')
        if (constrain_events or single_fault) and not forbid_events:
            old_events, new_events = _event_draft_schemas(contract)
            for kind, items in old_events.items():
                array = {'type': 'array', 'items': copy.deepcopy(items)}
                legacy['properties'][kind] = (array if kind == 'faults' else
                    {'anyOf': [array, {'type': 'object'}]})
            legacy['properties']['modifiers'] = {'type': 'object', 'properties': {
                kind: {'type': 'array', 'items': copy.deepcopy(items)}
                for kind, items in old_events.items()}, 'additionalProperties': False}
            canonical['properties']['events'] = {'type': 'object', 'properties': {
                kind: {'type': 'array', 'items': copy.deepcopy(items)}
                for kind, items in new_events.items()}, 'additionalProperties': False}
    if forbid_events:
        empty_array = {'type':'array','maxItems':0}
        empty_object = {'type':'object','properties':{},'additionalProperties':False}
        empty_legacy = {'anyOf':[empty_array,empty_object]}
        for kind in ('faults','degradations','constraints'):
            legacy['properties'][kind] = copy.deepcopy(empty_array if kind == 'faults' else empty_legacy)
        legacy['properties']['modifiers'] = {'type':'object','properties':{
            kind:copy.deepcopy(empty_array if kind == 'faults' else empty_legacy)
            for kind in ('faults','degradations','constraints')}, 'additionalProperties':False}
        canonical['properties']['events'] = {'type':'object','properties':{
            kind:copy.deepcopy(empty_array) for kind in ('faults','degradations','constraints')},
            'additionalProperties':False}
        if capability_id is not None:
            canonical['properties']['model']['properties']['legacy_degradations'] = copy.deepcopy(empty_object)
    # MissionSpec declares a string list. Typing this known field avoids
    # unconstrained empty-array parser forks without excluding extension keys.
    mission = {'type': 'object', 'properties': {
        'required_couplings': {'type': 'array', 'items': {'type': 'string'}}}}
    legacy['properties']['mission'] = copy.deepcopy(mission)
    canonical['properties']['mission'] = copy.deepcopy(mission)
    alternatives = [legacy, canonical]
    if single_fault and not forbid_events and capability_id is not None:
        # Full object alternatives are intentional: LM-format-enforcer does not
        # merge sibling constraints into nested anyOf branches. Restrict count
        # from the user's explicit instruction, never constrain answer values.
        empty = {'type': 'array', 'maxItems': 0}
        empty_object = {'type': 'object', 'properties': {}, 'additionalProperties': False}
        fault_target = {'type': 'object', 'properties': {
            'level': {'type': 'string'}, 'name': {'type': 'string'}, 'mode': {'const': 'fault'}},
            'required': ['mode'], 'additionalProperties': False}
        for kind in ('degradations', 'constraints'):
            legacy['properties'][kind] = {'anyOf': [copy.deepcopy(empty), copy.deepcopy(empty_object)]}
            legacy['properties']['modifiers']['properties'][kind] = copy.deepcopy(empty)
            canonical['properties']['events']['properties'][kind] = copy.deepcopy(empty)
        legacy['properties']['target'] = copy.deepcopy(fault_target)
        # Prefer one unambiguous spelling in generated legacy faults. The broad
        # parser/default transport still accepts aliases; generation must not
        # emit both severity and magnitude with contradictory values.
        fault_fields = copy.deepcopy(old_events['faults']['anyOf'][0]['properties'])
        for alias in ('severity', 'modifier_type', 'type', 'start_time_s', 'target_path'):
            fault_fields.pop(alias, None)
        fault_fields['parameters'] = {'type': 'object'}
        event_target = str(contract.get('target') or '')
        if event_target and not contract.get('event_target_prefixes'):
            fault_fields['target'] = {'type': 'string', 'enum': [event_target]}
        fault_item = {'type': 'object', 'properties': fault_fields,
                      'required': ['fault_type', 'target', 'onset_time_s', 'magnitude'],
                      'additionalProperties': False}
        legacy['properties']['faults']['items'] = copy.deepcopy(fault_item)
        legacy['properties']['modifiers']['properties']['faults']['items'] = copy.deepcopy(fault_item)
        canonical['properties']['model']['properties']['target'] = copy.deepcopy(fault_target)
        canonical['properties']['model']['required'].append('target')
        canonical['properties']['model']['properties']['legacy_degradations'] = copy.deepcopy(empty_object)
        canonical_faults = canonical['properties']['events']['properties']['faults']
        if event_target and not contract.get('event_target_prefixes'):
            canonical_faults['items']['properties']['target'] = {'type': 'string', 'enum': [event_target]}
        canonical_faults.update({'minItems': 1, 'maxItems': 1})
        canonical['properties']['events']['required'] = ['faults']
        canonical['required'].append('events')
        top, modifier = copy.deepcopy(legacy), copy.deepcopy(legacy)
        top['properties']['faults'].update({'minItems': 1, 'maxItems': 1})
        top['required'].append('faults')
        top['properties']['modifiers']['properties']['faults'] = copy.deepcopy(empty)
        modifier['properties']['faults'] = copy.deepcopy(empty)
        modifier['properties']['modifiers']['properties']['faults'].update({'minItems': 1, 'maxItems': 1})
        modifier['properties']['modifiers']['required'] = ['faults']
        modifier['required'].append('modifiers')
        alternatives = ([top, modifier, canonical] if single_fault_carrier == 'auto' else
                        [top, canonical] if single_fault_carrier == 'faults' else [modifier, canonical])
    if parameter_assignments:
        if capability_id is None:
            raise ValueError('Explicit parameter binding requires a registered capability')
        required = list(parameter_assignments)
        # Bind after event alternatives are finalized so single-fault branches
        # cannot restore a canonical object with no authoritative parameter carrier.
        bound_alternatives = []
        for alternative in alternatives:
            if alternative is not canonical:
                alternative['required'].append('parameters')
                alternative['properties']['parameters']['required'] = required
                bound_alternatives.append(alternative)
                continue
            # Every present carrier keeps its const constraints; each branch
            # additionally requires the carrier the runtime will actually use.
            values_branch = copy.deepcopy(canonical)
            values_branch['required'].append('parameters')
            values_branch['properties']['parameters']['required'] = ['values']
            values_branch['properties']['parameters']['properties']['values']['required'] = required
            config_branch = copy.deepcopy(canonical)
            config_branch['properties']['model']['required'].append('config')
            config_branch['properties']['model']['properties']['config']['required'] = required
            # model.config is the runtime fallback only for empty/absent values.
            config_branch['properties']['parameters']['properties']['values']['const'] = {}
            bound_alternatives.extend([values_branch, config_branch])
        alternatives = bound_alternatives
    direct = {"anyOf": alternatives}
    wrappers = [{"type": "object", "properties": {key: direct},
                 "required": [key], "additionalProperties": False}
                for key in ("task_spec", "taskspec", "TaskSpec", "spec")]
    return {"anyOf": [*alternatives, *wrappers]}


class OpenAIChatCompletionsBackend:
    """Generic OpenAI-compatible Chat Completions backend.

    This integration is intended for local vLLM/Qwen servers and enterprise
    gateways.  Authentication is optional: when ``api_key_env`` is unset or the
    environment variable is absent, no Authorization header is sent.
    """

    name = "openai_compatible"

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        api_key_env: str = "OPENAI_API_KEY",
        timeout_s: float = 60.0,
        temperature: float | None = None,
        seed: int | None = None,
        max_output_tokens: int | None = None,
        structured_output: str = "json_object",
        response_schema: Mapping[str, Any] | None = None,
    ) -> None:
        if not model:
            raise LLMBackendError("openai-compatible backend requires a model name")
        if not base_url:
            raise LLMBackendError("openai-compatible backend requires a base URL")
        self.model = model
        self.endpoint = self._normalize_endpoint(base_url)
        self.api_key_env = api_key_env
        self.timeout_s = float(timeout_s)
        self.temperature = temperature
        self.seed = seed
        self.max_output_tokens = max_output_tokens
        structured = (structured_output or "json_object").strip().lower()
        if structured not in {"text", "json_object", "json_schema"}:
            raise LLMBackendError("openai-compatible backend supports text, json_object or json_schema structured output")
        self.structured_output = structured
        self.response_schema = copy.deepcopy(dict(response_schema)) if response_schema is not None else None

    @staticmethod
    def _normalize_endpoint(base_url: str) -> str:
        url = base_url.rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        if url.endswith("/v1"):
            return f"{url}/chat/completions"
        return f"{url}/v1/chat/completions"

    @staticmethod
    def _extract_output_text(payload: Mapping[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, Mapping):
                message = first.get("message")
                if isinstance(message, Mapping) and isinstance(message.get("content"), str):
                    return str(message["content"]).strip()
                if isinstance(first.get("text"), str):
                    return str(first["text"]).strip()
        return ""

    def generate_text(self, prompt: str) -> LLMCallResult:
        started = time.perf_counter()
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": OPENAI_TASKSPEC_SYSTEM_PROMPT,
                },
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.seed is not None:
            body["seed"] = int(self.seed)
        if self.max_output_tokens is not None:
            body["max_tokens"] = int(self.max_output_tokens)
        if self.structured_output == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif self.structured_output == "json_schema":
            task_spec_schema = self.response_schema or taskspec_draft_response_schema()
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "sat_sim_task_spec",
                    "strict": False,
                    "schema": task_spec_schema,
                },
            }
        headers = {"Content-Type": "application/json"}
        api_key = os.environ.get(self.api_key_env) if self.api_key_env else None
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # nosec B310 - opt-in configured model endpoint
                raw = resp.read().decode("utf-8")
            payload = json.loads(raw)
            text = self._extract_output_text(payload)
            metadata = _call_metadata(prompt, {
                "model": self.model,
                "endpoint": self.endpoint,
                "response_id": payload.get("id"),
                "usage": payload.get("usage"),
                "seed": self.seed,
                "structured_output": self.structured_output,
                "structured_generation": payload.get('sat_sim',{}).get('structured_generation'),
                "authenticated": bool(api_key),
            }, started=started)
            if not text:
                choices = payload.get("choices") if isinstance(payload, Mapping) else None
                first = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
                message = first.get("message") if isinstance(first, Mapping) else {}
                reasoning = str(message.get("reasoning_content") or "") if isinstance(message, Mapping) else ""
                finish_reason = first.get("finish_reason") if isinstance(first, Mapping) else None
                metadata.update({"finish_reason": finish_reason, "reasoning_excerpt": reasoning[:500]})
                error = "response did not contain assistant content"
                if reasoning and finish_reason == "length":
                    error = "model exhausted the output budget in reasoning before producing final assistant content"
                return LLMCallResult(False, self.name, raw, len(prompt), metadata, error=error)
            return LLMCallResult(True, self.name, text, len(prompt), metadata)
        except urllib.error.HTTPError as exc:
            err_text = exc.read().decode("utf-8", errors="replace")
            lower_error = err_text.lower()
            context_overflow = any(marker in lower_error for marker in (
                "exceeds the available context size", "context length", "maximum context", "too many tokens", "context window"
            ))
            if context_overflow:
                estimated_tokens = max(1, len(prompt) // 4)
                return LLMCallResult(False, self.name, err_text, len(prompt), _call_metadata(prompt, {
                    "model": self.model, "status": exc.code, "endpoint": self.endpoint,
                    "estimated_prompt_tokens": estimated_tokens, "max_output_tokens": body.get("max_tokens"),
                    "reason_code": "MODEL_CONTEXT_WINDOW_EXCEEDED",
                }, started=started), error=f"model context window exceeded: prompt is about {estimated_tokens} tokens; reduce the prompt or increase the LM Studio context window")
            # Some OpenAI-compatible local servers reject response_format even though they can obey
            # a prompt-only JSON contract. Retry once only when the error is not a context overflow.
            rejected_output = any(reason in err_text for reason in (
                'MODEL_OUTPUT_OR_CONTEXT_REJECTED','MODEL_GENERATION_FAILED'))
            if exc.code in {400, 422} and "response_format" in body and not rejected_output:
                fallback_body = dict(body)
                fallback_body.pop("response_format", None)
                fallback_body["messages"] = [
                    {"role": "system", "content": "Return one valid JSON object only. Do not use Markdown fences."},
                    {"role": "user", "content": prompt},
                ]
                fallback_req = urllib.request.Request(
                    self.endpoint,
                    data=json.dumps(fallback_body).encode("utf-8"),
                    headers=headers,
                    method="POST",
                )
                try:
                    with urllib.request.urlopen(fallback_req, timeout=self.timeout_s) as resp:  # nosec B310
                        fallback_raw = resp.read().decode("utf-8")
                    fallback_payload = json.loads(fallback_raw)
                    text = self._extract_output_text(fallback_payload)
                    metadata = _call_metadata(prompt, {
                        "model": self.model, "endpoint": self.endpoint,
                        "response_id": fallback_payload.get("id"),
                        "usage": fallback_payload.get("usage"),
                        "structured_output": self.structured_output,
                        "structured_output_transport": "prompt_only_json",
                        "response_format_attempt": {"http_status": exc.code, "response_excerpt": err_text[:500]},
                        "authenticated": bool(api_key),
                    }, started=started)
                    if text:
                        return LLMCallResult(True, self.name, text, len(prompt), metadata)
                    return LLMCallResult(False, self.name, fallback_raw, len(prompt), metadata, error="fallback response did not contain assistant content")
                except Exception as fallback_exc:
                    return LLMCallResult(False, self.name, err_text, len(prompt), _call_metadata(prompt, {
                        "model": self.model, "status": exc.code, "endpoint": self.endpoint,
                        "response_format_rejected": True, "fallback_error": str(fallback_exc),
                    }, started=started), error=f"HTTP {exc.code}; prompt-only JSON fallback failed")
            return LLMCallResult(False, self.name, err_text, len(prompt), _call_metadata(prompt, {"model": self.model, "status": exc.code, "endpoint": self.endpoint}, started=started), error=f"HTTP {exc.code}")
        except Exception as exc:
            return LLMCallResult(False, self.name, "", len(prompt), _call_metadata(prompt, {"model": self.model, "endpoint": self.endpoint}, started=started), error=str(exc))


class DeepSeekChatCompletionsBackend:
    """Minimal dependency-free DeepSeek Chat Completions backend.

    DeepSeek's public API is OpenAI-format compatible.  A4 adds two structured
    output modes on top of the legacy text/YAML mode:

    ``json_object``
        Sends ``response_format={"type": "json_object"}`` and expects the
        assistant message content to be valid JSON.  The JSON may be either a
        TaskSpec object or ``{"task_spec": {...}}``.

    ``tool_call``
        Sends a single ``generate_task_spec`` tool with ``strict=True`` and
        extracts the tool-call arguments.  DeepSeek strict mode is beta; when no
        base URL is provided, sat-sim uses ``https://api.deepseek.com/beta`` as
        the base URL for this mode.  The returned arguments may again be either
        a TaskSpec object or ``{"task_spec": {...}}``.

    The backend still returns raw text only.  Local parsing, validation, repair,
    compilation, script export, and optional execution remain trusted sat-sim
    steps.
    """

    name = "deepseek"
    SUPPORTED_STRUCTURED_OUTPUTS = {"text", "json_object", "tool_call"}

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str = "DEEPSEEK_API_KEY",
        base_url: str = "https://api.deepseek.com",
        timeout_s: float = 60.0,
        temperature: float | None = None,
        max_output_tokens: int | None = None,
        structured_output: str = "text",
    ) -> None:
        if not model:
            raise LLMBackendError("deepseek backend requires --model or SAT_SIM_DEEPSEEK_MODEL")
        structured = (structured_output or "text").strip().lower()
        alias = {"json": "json_object", "function": "tool_call", "tool": "tool_call", "strict_tool": "tool_call"}
        structured = alias.get(structured, structured)
        if structured not in self.SUPPORTED_STRUCTURED_OUTPUTS:
            raise LLMBackendError(f"unsupported deepseek structured_output: {structured_output!r}")
        self.model = model
        self.api_key_env = api_key_env or "DEEPSEEK_API_KEY"
        if structured == "tool_call" and (base_url is None or str(base_url).rstrip("/") == "https://api.deepseek.com"):
            base_url = "https://api.deepseek.com/beta"
        self.endpoint = self._normalize_endpoint(base_url)
        self.timeout_s = float(timeout_s)
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.structured_output = structured

    @staticmethod
    def _normalize_endpoint(base_url: str | None) -> str:
        url = (base_url or "https://api.deepseek.com").rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        return f"{url}/chat/completions"

    @staticmethod
    def _unwrap_task_spec_payload(data: Any) -> Any:
        """Return a TaskSpec object from direct or wrapped JSON payloads."""

        if isinstance(data, Mapping):
            for key in ("task_spec", "taskspec", "TaskSpec", "spec"):
                nested = data.get(key)
                if isinstance(nested, Mapping):
                    return nested
        return data

    def _extract_tool_call_text(self, message: Mapping[str, Any]) -> str:
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list) or not tool_calls:
            return ""
        first = tool_calls[0]
        if not isinstance(first, Mapping):
            return ""
        function = first.get("function")
        if not isinstance(function, Mapping):
            return ""
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                data = json.loads(arguments)
            except Exception:
                return arguments.strip()
            unwrapped = self._unwrap_task_spec_payload(data)
            if isinstance(unwrapped, Mapping):
                return json.dumps(dict(unwrapped), ensure_ascii=False)
            return json.dumps(data, ensure_ascii=False)
        if isinstance(arguments, Mapping):
            unwrapped = self._unwrap_task_spec_payload(arguments)
            if isinstance(unwrapped, Mapping):
                return json.dumps(dict(unwrapped), ensure_ascii=False)
            return json.dumps(dict(arguments), ensure_ascii=False)
        return ""

    def _extract_output_text(self, payload: Mapping[str, Any]) -> str:
        choices = payload.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, Mapping):
                message = first.get("message")
                if isinstance(message, Mapping):
                    tool_text = self._extract_tool_call_text(message)
                    if tool_text:
                        return tool_text.strip()
                    content = message.get("content")
                    if isinstance(content, str):
                        return content.strip()
                text = first.get("text")
                if isinstance(text, str):
                    return text.strip()
        # Some OpenAI-compatible gateways return output_text.
        output_text = payload.get("output_text")
        if isinstance(output_text, str):
            return output_text.strip()
        return ""

    def _system_prompt(self) -> str:
        if self.structured_output == "json_object":
            return (
                "You generate only valid JSON. The JSON must be a sat-sim TaskSpec object "
                "or an object with a single task_spec property containing the TaskSpec. "
                "Do not output Markdown, YAML, comments, or Python code."
            )
        if self.structured_output == "tool_call":
            return (
                "Call the generate_task_spec tool exactly once with a complete sat-sim TaskSpec. "
                "Do not write Python code and do not include explanatory text."
            )
        return "You generate only a sat-sim TaskSpec YAML or JSON document. Do not output Python code."

    def _tool_schema(self) -> list[dict[str, Any]]:
        # A deliberately shallow strict schema: DeepSeek strict mode validates the
        # wrapper shape while sat-sim performs the authoritative TaskSpec
        # validation locally.  Keeping the tool schema shallow avoids duplicating
        # a large JSON Schema with optional capability-specific branches.
        return [
            {
                "type": "function",
                "function": {
                    "name": "generate_task_spec",
                    "description": "Return one sat-sim TaskSpec JSON object for the requested satellite simulation.",
                    "strict": True,
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "task_spec": {
                                "type": "object",
                                "description": "Complete sat-sim TaskSpec JSON object. Do not include Python code.",
                            }
                        },
                        "required": ["task_spec"],
                        "additionalProperties": False,
                    },
                },
            }
        ]

    def _request_body(self, prompt: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        if self.max_output_tokens is not None:
            # DeepSeek Chat Completions uses OpenAI-compatible max_tokens.
            body["max_tokens"] = int(self.max_output_tokens)
        if self.structured_output == "json_object":
            body["response_format"] = {"type": "json_object"}
        elif self.structured_output == "tool_call":
            body["tools"] = self._tool_schema()
            body["tool_choice"] = {"type": "function", "function": {"name": "generate_task_spec"}}
        return body

    def generate_text(self, prompt: str) -> LLMCallResult:
        started = time.perf_counter()
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            return LLMCallResult(False, self.name, "", len(prompt), _call_metadata(prompt, {"model": self.model, "api_key_env": self.api_key_env, "structured_output": self.structured_output}, started=started), error=f"missing API key env var: {self.api_key_env}")
        body = self._request_body(prompt)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_s) as resp:  # nosec B310 - opt-in API backend
                raw = resp.read().decode("utf-8")
            payload = json.loads(raw)
            text = self._extract_output_text(payload)
            metadata = _call_metadata(prompt, {
                "model": self.model,
                "endpoint": self.endpoint,
                "response_id": payload.get("id"),
                "usage": payload.get("usage"),
                "structured_output": self.structured_output,
                "response_format": body.get("response_format"),
                "tool_choice": body.get("tool_choice"),
            }, started=started)
            if not text:
                return LLMCallResult(False, self.name, raw, len(prompt), metadata, error="response did not contain assistant content or tool-call arguments")
            return LLMCallResult(True, self.name, text, len(prompt), metadata)
        except urllib.error.HTTPError as exc:
            err_text = exc.read().decode("utf-8", errors="replace")
            return LLMCallResult(False, self.name, err_text, len(prompt), _call_metadata(prompt, {"model": self.model, "status": exc.code, "endpoint": self.endpoint, "structured_output": self.structured_output}, started=started), error=f"HTTP {exc.code}")
        except Exception as exc:
            return LLMCallResult(False, self.name, "", len(prompt), _call_metadata(prompt, {"model": self.model, "endpoint": self.endpoint, "structured_output": self.structured_output}, started=started), error=str(exc))


def make_text_llm_backend(config: LLMCallConfig) -> TextLLMBackend:
    """Construct a text LLM backend from a config."""

    if config.backend == "command":
        if not config.command:
            raise LLMBackendError("backend='command' requires command")
        return CommandLLMBackend(config.command, timeout_s=config.timeout_s)
    if config.backend == "openai":
        model = config.model or os.environ.get("SAT_SIM_OPENAI_MODEL") or ""
        return OpenAIResponsesBackend(
            model=model,
            api_key_env=config.api_key_env,
            base_url=config.base_url or os.environ.get("SAT_SIM_OPENAI_BASE_URL") or "https://api.openai.com/v1/responses",
            timeout_s=config.timeout_s,
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
        )
    if config.backend in {"openai_compatible", "qwen", "vllm"}:
        model = config.model or os.environ.get("SAT_SIM_OPENAI_COMPATIBLE_MODEL") or ""
        base_url = config.base_url or os.environ.get("SAT_SIM_OPENAI_COMPATIBLE_BASE_URL") or ""
        return OpenAIChatCompletionsBackend(
            model=model,
            base_url=base_url,
            api_key_env=config.api_key_env,
            timeout_s=config.timeout_s,
            temperature=config.temperature,
            seed=config.seed,
            max_output_tokens=config.max_output_tokens,
            structured_output=config.structured_output or "json_object",
            response_schema=config.response_schema,
        )
    if config.backend == "deepseek":
        model = config.model or os.environ.get("SAT_SIM_DEEPSEEK_MODEL") or "deepseek-chat"
        # Preserve explicit API-key env overrides; otherwise use DeepSeek's own env var.
        api_key_env = config.api_key_env if config.api_key_env and config.api_key_env != "OPENAI_API_KEY" else "DEEPSEEK_API_KEY"
        structured_output = config.structured_output or os.environ.get("SAT_SIM_DEEPSEEK_STRUCTURED_OUTPUT") or "text"
        default_base = "https://api.deepseek.com/beta" if structured_output in {"tool_call", "tool", "strict_tool", "function"} else "https://api.deepseek.com"
        return DeepSeekChatCompletionsBackend(
            model=model,
            api_key_env=api_key_env,
            base_url=config.base_url or os.environ.get("SAT_SIM_DEEPSEEK_BASE_URL") or default_base,
            timeout_s=config.timeout_s,
            temperature=config.temperature,
            max_output_tokens=config.max_output_tokens,
            structured_output=structured_output,
        )
    raise LLMBackendError(f"unsupported text LLM backend: {config.backend}")


__all__ = [
    "CommandLLMBackend",
    "DeepSeekChatCompletionsBackend",
    "LLMBackendError",
    "LLMCallConfig",
    "LLMCallResult",
    "OpenAIResponsesBackend",
    "OpenAIChatCompletionsBackend",
    "TextLLMBackend",
    "make_text_llm_backend",
]
