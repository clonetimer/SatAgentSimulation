"""Architecture and schema-contract validation for the satellite simulation project.

The checker enforces the ownership chain::

    whole_spacecraft -> subsystem -> component -> Basilisk native object/message

It also ensures that component ``*Config``/``*Spec`` contracts live in
``schemas.py`` and that any public builder parameter not consumed by its
function body is explicitly tracked in the native-capability audit rather than
silently becoming a ghost parameter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import ast
from pathlib import Path
from typing import Any, Iterable

from .native_capability_audit import (
    BLOCKING_STATUSES,
    SURFACES,
    _find_unused_arguments,
    build_field_records,
    find_unregistered_config_surfaces,
)

SCHEMA_VERSION = "layer-schema-contract-v1.2"
BATCH = "LAYER-OWNERSHIP-CLOSURE-1+COMPONENT-SCHEMA-CONTRACT-1"


@dataclass(frozen=True)
class ContractIssue:
    code: str
    severity: str
    category: str
    path: str
    line: int
    message: str


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _imported_modules(tree: ast.AST) -> Iterable[tuple[str, int, tuple[str, ...]]]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno, ()
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            yield module, node.lineno, tuple(alias.name for alias in node.names)


def _call_name(node: ast.Call) -> str:
    target: ast.AST = node.func
    parts: list[str] = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts))


def _except_is_silent_pass(node: ast.ExceptHandler) -> bool:
    return len(node.body) == 1 and isinstance(node.body[0], ast.Pass)


def _scan_layer_ownership(project_root: Path) -> tuple[dict[str, Any], list[ContractIssue]]:
    src_root = project_root / "src"
    whole_root = src_root / "whole_spacecraft"
    issues: list[ContractIssue] = []
    direct_component_imports: list[dict[str, Any]] = []
    unauthorized_basilisk_imports: list[dict[str, Any]] = []
    native_constructor_calls: list[dict[str, Any]] = []
    silent_wiring_failures: list[dict[str, Any]] = []

    prohibited_native_constructors = {
        "ReactionWheelStateEffector",
        "ThrusterDynamicEffector",
        "FuelTank",
        "SimpleBattery",
        "SimplePowerSink",
        "SimpleSolarPanel",
        "SimpleInstrument",
        "SimpleStorageUnit",
        "SimpleTransmitter",
        "SpaceToGroundTransmitter",
        "ImuSensor",
        "StarTracker",
        "CoarseSunSensor",
        "Magnetometer",
        "MtbEffector",
        "VSCMGStateEffector",
    }

    for path in sorted(whole_root.rglob("*.py")):
        tree = _parse(path)
        rel = _rel(path, project_root)
        for module, line, members in _imported_modules(tree):
            if module == "components" or module.startswith("components."):
                row = {"path": rel, "line": line, "module": module, "members": list(members)}
                direct_component_imports.append(row)
                issues.append(ContractIssue(
                    "LAYER-WHOLE-COMPONENT-IMPORT", "ERROR", "layer_ownership", rel, line,
                    f"whole-spacecraft code imports component layer directly: {module}",
                ))
            if module == "Basilisk.simulation" or module.startswith("Basilisk.simulation."):
                allowed = rel == "src/whole_spacecraft/builder.py" and set(members) <= {"spacecraft"}
                if not allowed:
                    row = {"path": rel, "line": line, "module": module, "members": list(members)}
                    unauthorized_basilisk_imports.append(row)
                    issues.append(ContractIssue(
                        "LAYER-WHOLE-NATIVE-IMPORT", "ERROR", "layer_ownership", rel, line,
                        "whole-spacecraft may import only Basilisk spacecraft for the central bus; "
                        f"found {module} {members}",
                    ))

        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node)
                leaf = name.rsplit(".", 1)[-1]
                if leaf in prohibited_native_constructors:
                    allowed = rel == "src/whole_spacecraft/builder.py" and name.endswith("spacecraft.Spacecraft")
                    if not allowed:
                        row = {"path": rel, "line": node.lineno, "call": name}
                        native_constructor_calls.append(row)
                        issues.append(ContractIssue(
                            "LAYER-WHOLE-NATIVE-CONSTRUCTOR", "ERROR", "layer_ownership", rel, node.lineno,
                            f"whole-spacecraft directly constructs subsystem component: {name}",
                        ))
            elif isinstance(node, ast.ExceptHandler) and _except_is_silent_pass(node):
                row = {"path": rel, "line": node.lineno}
                silent_wiring_failures.append(row)
                issues.append(ContractIssue(
                    "LAYER-WHOLE-SILENT-EXCEPT", "ERROR", "error_handling", rel, node.lineno,
                    "whole-spacecraft code must not silently suppress wiring/runtime errors",
                ))

    # Required component/subsystem construction and wiring helpers must fail
    # explicitly.  Runtime telemetry readers may intentionally tolerate an
    # absent optional input, so this gate is scoped to build/attach/wire/create
    # functions rather than every exception handler in the lower layers.
    construction_prefixes = ("build_", "attach_", "wire_", "connect_", "create_")
    seen_silent_helpers: set[tuple[str, int, str]] = set()
    for layer_name in ("components", "subsystems"):
        for path in sorted((src_root / layer_name).rglob("*.py")):
            tree = _parse(path)
            rel = _rel(path, project_root)
            for function in ast.walk(tree):
                if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                normalized_name = function.name.lstrip("_")
                if not normalized_name.startswith(construction_prefixes):
                    continue
                for node in ast.walk(function):
                    if not isinstance(node, ast.ExceptHandler) or not _except_is_silent_pass(node):
                        continue
                    key = (rel, node.lineno, function.name)
                    if key in seen_silent_helpers:
                        continue
                    seen_silent_helpers.add(key)
                    row = {"path": rel, "line": node.lineno, "function": function.name, "layer": layer_name}
                    silent_wiring_failures.append(row)
                    issues.append(ContractIssue(
                        "LAYER-CONSTRUCTION-SILENT-EXCEPT", "ERROR", "error_handling", rel, node.lineno,
                        f"{layer_name} construction/wiring helper {function.name} silently suppresses an error",
                    ))

    injector_path = whole_root / "_runtime_fault_injector.py"
    injector_tree = _parse(injector_path)
    injector_methods = {
        node.name
        for node in ast.walk(injector_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    physical_fault_methods = sorted(
        name for name in injector_methods
        if name.startswith("_inject_") and name != "_inject_fault"
    )
    for name in physical_fault_methods:
        node = next(
            item for item in ast.walk(injector_tree)
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name
        )
        issues.append(ContractIssue(
            "LAYER-WHOLE-PHYSICAL-FAULT-METHOD", "ERROR", "fault_ownership",
            _rel(injector_path, project_root), node.lineno,
            f"whole-spacecraft FaultInjector implements component-specific physics method {name}",
        ))

    injector_source = injector_path.read_text(encoding="utf-8")
    router_path = src_root / "subsystems" / "runtime_fault_router.py"
    router_source = router_path.read_text(encoding="utf-8") if router_path.exists() else ""
    required_subsystems = ("adcs", "eps", "propulsion", "payload", "comm_data", "thermal")
    router_missing = [name for name in required_subsystems if f'subsystem == "{name}"' not in router_source]
    route_contract_ok = (
        "from subsystems.runtime_fault_router import route_runtime_fault" in injector_source
        and "route_runtime_fault(" in injector_source
        and not physical_fault_methods
        and not router_missing
    )
    if not route_contract_ok:
        issues.append(ContractIssue(
            "LAYER-RUNTIME-FAULT-ROUTE", "ERROR", "fault_ownership",
            _rel(injector_path, project_root), 1,
            f"runtime fault route is incomplete; missing subsystem branches={router_missing}",
        ))

    return {
        "whole_component_imports": direct_component_imports,
        "whole_unauthorized_basilisk_simulation_imports": unauthorized_basilisk_imports,
        "whole_native_component_constructor_calls": native_constructor_calls,
        "silent_builder_or_whole_exceptions": silent_wiring_failures,
        "runtime_fault_router": {
            "status": "PASS" if route_contract_ok else "FAIL",
            "required_subsystems": list(required_subsystems),
            "missing_subsystems": router_missing,
            "whole_physical_fault_methods": physical_fault_methods,
            "ownership_chain": "whole_spacecraft -> runtime_fault_router -> subsystem faults -> component faults",
        },
        "central_bus_native_exception": {
            "path": "src/whole_spacecraft/builder.py",
            "allowed_native_constructor": "spacecraft.Spacecraft",
            "reason": "the whole-spacecraft layer owns the central dynamics bus",
        },
    }, issues


def _scan_schema_contract(project_root: Path) -> tuple[dict[str, Any], list[ContractIssue]]:
    src_root = project_root / "src"
    component_root = src_root / "components"
    issues: list[ContractIssue] = []
    builder_contract_definitions: list[dict[str, Any]] = []
    schema_builder_cycles: list[dict[str, Any]] = []
    missing_schema_imports: list[dict[str, Any]] = []
    registered_ghost_parameters: list[dict[str, Any]] = []
    unregistered_ghost_parameters: list[dict[str, Any]] = []
    schema_surfaces: list[dict[str, Any]] = []
    missing_module_docstrings: list[dict[str, Any]] = []

    for component_dir in sorted(path for path in component_root.iterdir() if path.is_dir()):
        builder_path = component_dir / "builder.py"
        schema_path = component_dir / "schemas.py"
        schema_classes: list[str] = []
        if schema_path.exists():
            schema_tree = _parse(schema_path)
            if ast.get_docstring(schema_tree) is None:
                row = {"path": _rel(schema_path, project_root), "line": 1, "kind": "schemas"}
                missing_module_docstrings.append(row)
                issues.append(ContractIssue(
                    "SCHEMA-MODULE-DOCSTRING", "ERROR", "schema_contract", row["path"], 1,
                    "component schemas.py must have a module docstring",
                ))
            schema_classes = sorted(
                node.name for node in ast.walk(schema_tree)
                if isinstance(node, ast.ClassDef) and (node.name.endswith("Config") or node.name.endswith("Spec"))
            )
            for module, line, _members in _imported_modules(schema_tree):
                if module == ".builder" or module.endswith(".builder") or module == "builder":
                    row = {"path": _rel(schema_path, project_root), "line": line, "module": module}
                    schema_builder_cycles.append(row)
                    issues.append(ContractIssue(
                        "SCHEMA-IMPORT-CYCLE", "ERROR", "schema_contract", row["path"], line,
                        "schemas.py must not import builder.py",
                    ))
        if builder_path.exists():
            builder_tree = _parse(builder_path)
            if ast.get_docstring(builder_tree) is None:
                row = {"path": _rel(builder_path, project_root), "line": 1, "kind": "builder"}
                missing_module_docstrings.append(row)
                issues.append(ContractIssue(
                    "SCHEMA-MODULE-DOCSTRING", "ERROR", "schema_contract", row["path"], 1,
                    "component builder.py must have a module docstring before imports",
                ))
            for node in ast.walk(builder_tree):
                if isinstance(node, ast.ClassDef) and (node.name.endswith("Config") or node.name.endswith("Spec")):
                    row = {"path": _rel(builder_path, project_root), "line": node.lineno, "class": node.name}
                    builder_contract_definitions.append(row)
                    issues.append(ContractIssue(
                        "SCHEMA-CONTRACT-IN-BUILDER", "ERROR", "schema_contract", row["path"], node.lineno,
                        f"component contract {node.name} must live in schemas.py",
                    ))
            if schema_classes:
                imports_own_schema = any(
                    module in {"schemas", f"components.{component_dir.name}.schemas"}
                    or module.endswith(f"{component_dir.name}.schemas")
                    or (isinstance(node, ast.ImportFrom) and node.level > 0 and (node.module or "") == "schemas")
                    for node in ast.walk(builder_tree)
                    if isinstance(node, (ast.Import, ast.ImportFrom))
                    for module in ([node.module or ""] if isinstance(node, ast.ImportFrom) else [alias.name for alias in node.names])
                )
                if not imports_own_schema:
                    row = {
                        "path": _rel(builder_path, project_root),
                        "line": 1,
                        "component": component_dir.name,
                        "schema_classes": schema_classes,
                    }
                    missing_schema_imports.append(row)
                    issues.append(ContractIssue(
                        "SCHEMA-BUILDER-NOT-USING-CONTRACT", "ERROR", "schema_contract", row["path"], 1,
                        f"builder does not import its own schemas.py contract: {schema_classes}",
                    ))
        if schema_classes:
            schema_surfaces.append({
                "component": component_dir.name,
                "path": _rel(schema_path, project_root),
                "classes": schema_classes,
            })

    # Ghost parameters are permitted only when they are explicitly registered
    # in the field-level native audit as blocking remediation.  This keeps known
    # debt visible while preventing any new silent parameter from entering.
    audit_records, audit_failures = build_field_records(project_root)
    audit_by_key = {
        (row.get("project_path"), row.get("surface"), row.get("field")): row
        for row in audit_records
        if row.get("record_kind") == "project_field"
    }
    for failure in audit_failures:
        issues.append(ContractIssue(
            "SCHEMA-NATIVE-AUDIT-REGISTRY", "ERROR", "schema_contract",
            "src/sat_sim/validation/native_capability_audit.py", 1, failure,
        ))
    for surface in SURFACES:
        if surface.symbol_kind != "function":
            continue
        unused = _find_unused_arguments(project_root, surface.path, surface.symbol)
        for field in unused:
            row = audit_by_key.get((surface.path, surface.symbol, field))
            detail = {
                "path": surface.path,
                "function": surface.symbol,
                "parameter": field,
                "audit_status": row.get("project_status") if row else None,
                "audit_action": row.get("acceptance_action") if row else None,
            }
            if row and row.get("project_status") in BLOCKING_STATUSES and row.get("acceptance_action") == "must_fix":
                registered_ghost_parameters.append(detail)
            else:
                unregistered_ghost_parameters.append(detail)
                issues.append(ContractIssue(
                    "SCHEMA-UNREGISTERED-GHOST-PARAMETER", "ERROR", "schema_contract",
                    surface.path, 1,
                    f"{surface.symbol} parameter {field!r} is unused and not registered as must-fix debt",
                ))

    coverage = find_unregistered_config_surfaces(project_root)
    for category in ("unregistered", "stale", "duplicates", "missing_files"):
        for item in coverage.get(category, []):
            issues.append(ContractIssue(
                "SCHEMA-NATIVE-AUDIT-COVERAGE", "ERROR", "schema_contract", str(item), 1,
                f"native capability audit schema coverage issue ({category}): {item}",
            ))

    return {
        "schema_surfaces": schema_surfaces,
        "builder_contract_definitions": builder_contract_definitions,
        "schema_builder_import_cycles": schema_builder_cycles,
        "builders_missing_own_schema_import": missing_schema_imports,
        "registered_known_ghost_parameters": registered_ghost_parameters,
        "unregistered_ghost_parameters": unregistered_ghost_parameters,
        "missing_module_docstrings": missing_module_docstrings,
        "native_audit_config_surface_coverage": coverage,
        "policy": {
            "contract_location": "src/components/<component>/schemas.py",
            "builder_role": "construct native object and wire component-local messages",
            "known_ghost_rule": "unused public parameters must be registered as must_fix in native capability audit",
            "silent_ignore_rule": "required construction and message wiring errors must be explicit",
        },
    }, issues


def build_layer_schema_contract(project_root: Path) -> dict[str, Any]:
    project_root = Path(project_root).resolve()
    layer, layer_issues = _scan_layer_ownership(project_root)
    schema, schema_issues = _scan_schema_contract(project_root)
    issues = [*layer_issues, *schema_issues]
    errors = [issue for issue in issues if issue.severity == "ERROR"]
    warnings = [issue for issue in issues if issue.severity == "WARNING"]
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "status": "PASS" if not errors else "FAIL",
        "summary": {
            "error_count": len(errors),
            "warning_count": len(warnings),
            "whole_component_import_count": len(layer["whole_component_imports"]),
            "whole_unauthorized_native_import_count": len(layer["whole_unauthorized_basilisk_simulation_imports"]),
            "whole_native_constructor_count": len(layer["whole_native_component_constructor_calls"]),
            "silent_builder_or_whole_exception_count": len(layer["silent_builder_or_whole_exceptions"]),
            "silent_construction_or_wiring_exception_count": len(layer["silent_builder_or_whole_exceptions"]),
            "component_schema_surface_count": len(schema["schema_surfaces"]),
            "builder_contract_definition_count": len(schema["builder_contract_definitions"]),
            "schema_import_cycle_count": len(schema["schema_builder_import_cycles"]),
            "builder_missing_schema_import_count": len(schema["builders_missing_own_schema_import"]),
            "registered_known_ghost_parameter_count": len(schema["registered_known_ghost_parameters"]),
            "unregistered_ghost_parameter_count": len(schema["unregistered_ghost_parameters"]),
            "missing_component_module_docstring_count": len(schema["missing_module_docstrings"]),
            "runtime_fault_router_status": layer["runtime_fault_router"]["status"],
        },
        "layer_ownership": layer,
        "component_schema_contract": schema,
        "issues": [asdict(issue) for issue in issues],
    }


__all__ = ["BATCH", "SCHEMA_VERSION", "ContractIssue", "build_layer_schema_contract"]
