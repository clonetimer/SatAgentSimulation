"""Machine-readable capability registry for Agent-callable simulation tools."""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

import importlib
import json
import re
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping, Sequence

from .task_spec import TaskSpecError
from .task_validator import ValidationIssue
from .capability_composition import contract_interface_summary, normalize_dependencies
from .source_native import public_source_binding_payload
from .operator_contract import OperatorContract, OperatorContractIssue, normalize_operator_contract, validate_operator_contract
from .capability_integration import (
    CapabilityIntegrationContract,
    CapabilityIntegrationIssue,
    normalize_capability_integration,
    validate_capability_integration,
)


@dataclass(frozen=True)
class CapabilityContract:
    """Loaded capability contract."""

    capability_id: str
    data: dict[str, Any]
    path: Path | None = None

    @property
    def adapter_class_path(self) -> str:
        adapter = self.data.get("adapter") if isinstance(self.data.get("adapter"), Mapping) else {}
        value = adapter.get("class_path")
        if not isinstance(value, str) or not value.strip():
            raise TaskSpecError(f"capability {self.capability_id!r} does not define adapter.class_path")
        return value

    @property
    def target_level(self) -> str:
        return str(self.data.get("level", ""))

    @property
    def target_name(self) -> str:
        return str(self.data.get("target", ""))

    @property
    def trust_level(self) -> str:
        return str(self.data.get("trust_level", "unknown"))

    @property
    def lifecycle(self) -> dict[str, Any]:
        payload = self.data.get("lifecycle")
        if isinstance(payload, Mapping):
            return dict(payload)
        return {"status": "active", "replacement": None, "exposed_to_agent": True, "exposed_to_real_llm_eval": True}

    @property
    def lifecycle_status(self) -> str:
        return str(self.lifecycle.get("status") or "active")

    @property
    def replacement_capability_id(self) -> str | None:
        value = self.lifecycle.get("replacement")
        return str(value) if isinstance(value, str) and value.strip() else None

    @property
    def exposed_to_agent(self) -> bool:
        return bool(self.lifecycle.get("exposed_to_agent", self.lifecycle_status == "active"))

    @property
    def exposed_to_real_llm_eval(self) -> bool:
        return bool(self.lifecycle.get("exposed_to_real_llm_eval", self.exposed_to_agent))

    @property
    def is_active(self) -> bool:
        return self.lifecycle_status == "active"

    @property
    def product_tier(self) -> str:
        value = str(self.lifecycle.get("product_tier") or "").strip().lower()
        if value:
            return value
        if self.lifecycle_status == "active":
            return "recommended" if bool(self.lifecycle.get("recommended")) else "standard"
        if self.lifecycle_status in {"deprecated", "internal", "archived"}:
            return "compatibility"
        if self.lifecycle_status == "blocked":
            return "blocked"
        return "explicit"

    @property
    def recommended(self) -> bool:
        return self.is_active and self.product_tier == "recommended"

    @property
    def dependencies(self) -> list[dict[str, Any]]:
        return normalize_dependencies(self.data)

    @property
    def interface_summary(self) -> dict[str, Any]:
        return contract_interface_summary(self.data)

    @property
    def operator_contract(self) -> OperatorContract:
        """Return the normalized executable operator view."""

        return normalize_operator_contract(self.data)

    @property
    def integration_contract(self) -> CapabilityIntegrationContract:
        """Return the explicit model-library and physical-coupling boundary."""

        return normalize_capability_integration(self.data)

    @property
    def supported_couplings(self) -> frozenset[str]:
        return frozenset(self.integration_contract.supported_couplings)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.data)


def _load_yaml_text(text: str) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise TaskSpecError("capability registry requires PyYAML") from exc
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise TaskSpecError("capability contract root must be a mapping")
    return data


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []
    # Editable repository root: src/sat_sim/capability_registry.py -> repo root.
    dirs.append(Path(__file__).resolve().parents[2] / "capabilities")
    # Current working tree fallback.
    dirs.append(Path.cwd() / "capabilities")
    return dirs


def _package_contracts() -> list[CapabilityContract]:
    contracts: list[CapabilityContract] = []
    try:
        root = resources.files("sat_sim.capabilities")
        for item in root.iterdir():
            if item.name.startswith("_"):
                continue
            if item.name.endswith((".yaml", ".yml")):
                data = _load_yaml_text(item.read_text(encoding="utf-8"))
                if not isinstance(data.get("capability_id"), str):
                    continue
                cid = str(data.get("capability_id", item.name.rsplit(".", 1)[0]))
                contracts.append(CapabilityContract(cid, data, None))
    except Exception as exc:
        record_runtime_diagnostic(
            code='PACKAGED_CAPABILITY_REGISTRY_LOAD_FAILED',
            category=DiagnosticCategory.REGISTRY_SCHEMA_FAILURE,
            location='src/sat_sim/capability_registry.py:_package_contracts:01',
            exception=exc,
            strict=None,
        )
    return contracts


def _filesystem_contracts() -> list[CapabilityContract]:
    contracts: list[CapabilityContract] = []
    seen_paths: set[Path] = set()
    for directory in _candidate_dirs():
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.y*ml")):
            if path.name.startswith("_"):
                continue
            rp = path.resolve()
            if rp in seen_paths:
                continue
            seen_paths.add(rp)
            data = _load_yaml_text(path.read_text(encoding="utf-8"))
            if not isinstance(data.get("capability_id"), str):
                continue
            cid = str(data.get("capability_id", path.name.rsplit(".", 1)[0]))
            contracts.append(CapabilityContract(cid, data, path))
    return contracts



_CAPABILITY_LIST_CACHE: dict[tuple[str, bool], list[CapabilityContract]] = {}


def clear_capability_cache() -> None:
    """Clear the in-process capability registry cache.

    The registry is effectively static during normal CLI/test execution, but
    tests or development scripts that rewrite capability YAML files can call
    this helper before listing capabilities again.
    """

    _CAPABILITY_LIST_CACHE.clear()

def list_capabilities(*, include_experimental: bool = True) -> list[CapabilityContract]:
    """List known capability contracts, package first and filesystem overriding.

    A-1 caches the loaded YAML contracts per current working directory.  The
    Agent prompt builder calls the registry repeatedly during one generation
    session; re-parsing every YAML file each time is slow and has proven brittle
    in constrained tool environments.
    """

    cache_key = (str(Path.cwd().resolve()), bool(include_experimental))
    cached = _CAPABILITY_LIST_CACHE.get(cache_key)
    if cached is not None:
        return list(cached)

    merged: dict[str, CapabilityContract] = {}
    for contract in _package_contracts() + _filesystem_contracts():
        if not include_experimental and contract.trust_level in {"experimental", "draft"}:
            continue
        merged[contract.capability_id] = contract
    out = [merged[key] for key in sorted(merged)]
    _CAPABILITY_LIST_CACHE[cache_key] = out
    return list(out)


def get_capability(capability_id: str) -> CapabilityContract:
    for contract in list_capabilities():
        if contract.capability_id == capability_id:
            return contract
    known = ", ".join(c.capability_id for c in list_capabilities())
    raise TaskSpecError(f"unknown capability_id {capability_id!r}; known: {known}")


def active_capability_ids(*, real_llm_eval: bool = False) -> tuple[str, ...]:
    """Return active product capability IDs.

    Deprecated and internal compatibility capabilities remain loadable through
    ``list_capabilities``/``get_capability`` but are intentionally excluded from
    product-facing Agent prompts and planner defaults.
    """

    out: list[str] = []
    for contract in list_capabilities():
        if not contract.is_active:
            continue
        if real_llm_eval and not contract.exposed_to_real_llm_eval:
            continue
        if not real_llm_eval and not contract.exposed_to_agent:
            continue
        out.append(contract.capability_id)
    return tuple(sorted(out))


def explicit_capability_ids_in_text(
    text: str,
    *,
    allowed_capabilities: Sequence[str] | None = None,
) -> tuple[str, ...]:
    """Return exact capability IDs explicitly present in user text.

    Exact IDs are an authoritative routing instruction.  The boundary checks
    avoid treating an ID embedded inside a longer identifier as an exact match.
    Multiple IDs are returned rather than silently choosing one so callers can
    fail closed or request clarification.
    """

    haystack = str(text or "")
    allowed = tuple(allowed_capabilities) if allowed_capabilities is not None else active_capability_ids()
    matches: list[str] = []
    for capability_id in allowed:
        # A trailing full stop is sentence punctuation when followed by
        # whitespace/end, but remains part of an identifier in ``.v2``.
        pattern = (
            rf"(?<![A-Za-z0-9_.-]){re.escape(capability_id)}"
            rf"(?![A-Za-z0-9_-]|\.(?=[A-Za-z0-9_-]))"
        )
        if re.search(pattern, haystack):
            matches.append(capability_id)
    return tuple(sorted(set(matches)))


def legacy_compatibility_capability_ids() -> tuple[str, ...]:
    """Return the full catalog for explicit legacy/compatibility runs."""

    return tuple(sorted(contract.capability_id for contract in list_capabilities()))


def deprecated_capability_replacements() -> dict[str, str]:
    """Return deprecated/internal capability replacement mapping."""

    out: dict[str, str] = {}
    for contract in list_capabilities():
        replacement = contract.replacement_capability_id
        if contract.lifecycle_status in {"deprecated", "internal", "archived"} and replacement:
            out[contract.capability_id] = replacement
    return out


def get_replacement_capability_id(capability_id: str) -> str | None:
    return deprecated_capability_replacements().get(capability_id)


def resolve_capability_id_for_product(capability_id: str, *, allowed_capabilities: Sequence[str] | None = None) -> tuple[str, dict[str, Any] | None]:
    """Resolve deprecated/internal IDs to active replacement IDs for product paths.

    Returns ``(resolved_capability_id, migration_payload_or_none)``.  The
    replacement is only applied when it is present in the allowed capability set;
    explicit legacy compatibility runs can still use the original ID by passing a
    legacy/full allowed set and handling the original ID before this helper.
    """

    allowed = set(allowed_capabilities or active_capability_ids())
    try:
        contract = get_capability(capability_id)
    except Exception:
        return capability_id, None
    replacement = contract.replacement_capability_id
    if contract.lifecycle_status in {"deprecated", "internal", "archived"} and replacement and replacement in allowed:
        return replacement, {
            "from": capability_id,
            "to": replacement,
            "reason": "deprecated_replacement" if contract.lifecycle_status == "deprecated" else "internal_replacement",
            "lifecycle_status": contract.lifecycle_status,
        }
    return capability_id, None


def load_object(class_path: str) -> Any:
    module_name, _, attr = class_path.rpartition(".")
    if not module_name or not attr:
        raise TaskSpecError(f"invalid class path: {class_path!r}")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def get_adapter_for_capability(capability_id: str) -> Any:
    contract = get_capability(capability_id)
    cls = load_object(contract.adapter_class_path)
    return cls()


def capability_summary_payload() -> list[dict[str, Any]]:
    """Compact payload for CLI/Agent tool calls."""

    out: list[dict[str, Any]] = []
    for c in list_capabilities():
        modes = c.data.get("modes") if isinstance(c.data.get("modes"), Mapping) else {}
        interface = c.interface_summary
        out.append({
            "capability_id": c.capability_id,
            "level": c.target_level,
            "target": c.target_name,
            "domain": c.data.get("domain"),
            "trust_level": c.trust_level,
            "adapter": c.adapter_class_path,
            "basilisk_required": bool((c.data.get("implementation") or {}).get("basilisk_required", False)) if isinstance(c.data.get("implementation"), Mapping) else False,
            "modes": [name for name, payload in modes.items() if isinstance(payload, Mapping) and payload.get("supported")],
            "dependencies": interface["dependencies"],
            "consumes": interface["consumes"],
            "produces": interface["produces"],
            "time_grid": interface["time_grid"],
            "trace_fields": interface["trace_fields"],
            "source_binding": public_source_binding_payload(c.data),
            "lifecycle": c.lifecycle,
            "product_tier": c.product_tier,
            "recommended": c.recommended,
            "operator": c.operator_contract.model_dump(mode="json"),
            "integration_contract": c.integration_contract.model_dump(mode="json"),
            "model_projection": dict(c.data.get("model_projection") or {}) if isinstance(c.data.get("model_projection"), Mapping) else {},
            "execution": dict(c.data.get("execution") or {}) if isinstance(c.data.get("execution"), Mapping) else {},
        })
    return out



def operator_contract_summary_payload(*, active_only: bool = False) -> list[dict[str, Any]]:
    """Return normalized V22 operator contracts for Agent/planner tooling."""

    contracts = list_capabilities()
    if active_only:
        contracts = [c for c in contracts if c.is_active and c.exposed_to_agent]
    return [c.operator_contract.model_dump(mode="json") for c in contracts]


def validate_operator_registry(*, active_only: bool = False) -> list[OperatorContractIssue]:
    """Validate all normalized operator contracts."""

    issues: list[OperatorContractIssue] = []
    for contract in list_capabilities():
        if active_only and not (contract.is_active and contract.exposed_to_agent):
            continue
        try:
            issues.extend(validate_operator_contract(contract.operator_contract))
        except Exception as exc:
            issues.append(OperatorContractIssue(
                "error", contract.capability_id, "$", "OPERATOR_CONTRACT_INVALID", str(exc)
            ))
    return issues


def validate_capability_integration_registry(*, active_only: bool = False) -> list[CapabilityIntegrationIssue]:
    """Validate explicit source-module and coupling declarations for all capabilities."""

    issues: list[CapabilityIntegrationIssue] = []
    for contract in list_capabilities():
        if active_only and not (contract.is_active and contract.exposed_to_agent):
            continue
        issues.extend(validate_capability_integration(contract.data))
    return issues


def capability_integration_summary_payload(*, active_only: bool = False) -> list[dict[str, Any]]:
    contracts = list_capabilities()
    if active_only:
        contracts = [item for item in contracts if item.is_active and item.exposed_to_agent]
    return [item.integration_contract.model_dump(mode="json") for item in contracts]


def find_effect_owners(effect_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
    """Find registry-authoritative owners for one fault/degradation effect."""

    needle = effect_id.strip().lower()
    out: list[dict[str, Any]] = []
    for contract in list_capabilities():
        if active_only and not (contract.is_active and contract.exposed_to_agent):
            continue
        operator = contract.operator_contract
        for effect in operator.effects:
            names = {effect.effect_id.lower(), *(alias.lower() for alias in effect.aliases)}
            if needle in names:
                out.append({
                    "capability_id": contract.capability_id,
                    "level": operator.level,
                    "owner": effect.owner,
                    "effect": effect.model_dump(mode="json"),
                })
    return out


def validate_spec_against_capability(spec: Mapping[str, Any]) -> list[ValidationIssue]:
    """Run capability-specific adapter validation when ``capability_id`` is present."""

    model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
    capability_id = spec.get("capability_id") or model.get("capability_id")
    if capability_id is None:
        return []
    if not isinstance(capability_id, str) or not capability_id.strip():
        return [ValidationIssue("error", "$.capability_id", "must be a non-empty string", "capability")]
    try:
        contract = get_capability(capability_id)
        if contract.lifecycle_status == "blocked":
            return [ValidationIssue(
                "error", "$.model.capability_id",
                f"capability {capability_id!r} is blocked by lifecycle governance",
                "CAPABILITY_BLOCKED",
            )]
        adapter = get_adapter_for_capability(capability_id)
        issues = list(adapter.validate(spec, contract.data))

        # Parameter-consumption audit: every canonical parameters.values key must
        # be declared by the selected capability.  Unknown keys are blocked
        # rather than silently ignored/defaulted by an adapter.
        parameter_contract = contract.data.get("parameters") if isinstance(contract.data.get("parameters"), Mapping) else {}
        parameter_spec = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        parameter_values = parameter_spec.get("values") if isinstance(parameter_spec.get("values"), Mapping) else {}
        allowed_parameter_names = {str(name) for name in parameter_contract}
        for name in sorted(str(key) for key in parameter_values if str(key) not in allowed_parameter_names):
            issues.append(ValidationIssue(
                "error", f"$.parameters.values.{name}",
                f"parameter {name!r} is not declared/consumed by capability {capability_id!r}",
                "UNCONSUMED_PARAMETER",
            ))

        implementation = contract.data.get("implementation") if isinstance(contract.data.get("implementation"), Mapping) else {}
        simulation = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        assurance = spec.get("assurance") if isinstance(spec.get("assurance"), Mapping) else {}
        accepted = implementation.get("accepted_task_backends")
        if isinstance(accepted, Sequence) and not isinstance(accepted, (str, bytes)):
            accepted_values = {str(value) for value in accepted}
            requested = str(simulation.get("backend") or "")
            if requested and requested not in accepted_values:
                issues.append(ValidationIssue(
                    "error", "$.simulation.backend",
                    f"backend {requested!r} is incompatible with capability {capability_id!r}; accepted: {sorted(accepted_values)}",
                    "BACKEND_CAPABILITY_MISMATCH",
                ))
        if bool(implementation.get("requires_allow_proxy")) and not bool(assurance.get("allow_proxy", False)):
            issues.append(ValidationIssue(
                "error", "$.assurance.allow_proxy",
                f"capability {capability_id!r} uses {implementation.get('backend_type', 'a project bridge')} and requires allow_proxy=true",
                "CAPABILITY_PROXY_NOT_ALLOWED",
            ))
        return issues
    except Exception as exc:
        return [ValidationIssue("error", "$.capability_id", str(exc), "capability")]


def write_capability_inventory(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(capability_summary_payload(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


__all__ = [
    "CapabilityContract",
    "clear_capability_cache",
    "list_capabilities",
    "active_capability_ids",
    "legacy_compatibility_capability_ids",
    "deprecated_capability_replacements",
    "get_replacement_capability_id",
    "resolve_capability_id_for_product",
    "get_capability",
    "get_adapter_for_capability",
    "capability_summary_payload",
    "operator_contract_summary_payload",
    "validate_operator_registry",
    "validate_capability_integration_registry",
    "capability_integration_summary_payload",
    "find_effect_owners",
    "validate_spec_against_capability",
    "write_capability_inventory",
]
