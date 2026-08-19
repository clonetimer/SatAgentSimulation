"""Inventory utilities for mapping original ``src`` modules to Agent capabilities.

S1 is intentionally descriptive: it inventories what the original source tree
contains and records how much of it is currently exposed through capability
contracts.  It does not increase simulation fidelity and does not execute demo
runners.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .source_native import source_binding_payload


SOURCE_INVENTORY_SCHEMA_VERSION = "s1.source_inventory.v1"
COVERAGE_INDEX_SCHEMA_VERSION = "c1.coverage_index.v2"

EXCLUDED_SOURCE_MODULES = {
    "subsystems.adcs_actuators",
    "subsystems.adcs_control",
    "subsystems.adcs_sensors",
    "subsystems.adcs_chain",
    "subsystems.adcs_cmg_steering",
    "subsystems.adcs_magnetic_detumble",
    "subsystems.adcs_mtb_detumble_closed_loop",
}
MODIFIER_SOURCE_MODULES = set()  # component-local faults.py/degradation.py are detected per component
FOCUSED_ADCS_SOURCE_MODULES = {"subsystems.adcs", "subsystems.adcs_closed_loop"}


@dataclass(frozen=True)
class SourceModuleRecord:
    path: str
    module: str
    layer: str
    name: str
    has_model: bool
    has_builder: bool
    has_runner: bool
    has_schemas: bool
    has_basilisk: bool
    has_faults: bool
    has_degradation: bool
    classification: str
    recommended_exposure: str
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _module_name(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root)
    return ".".join(rel.parts)


def _package_dirs(src_root: Path, parent: str) -> Iterable[Path]:
    root = src_root / parent
    if not root.exists():
        return []
    return [p for p in sorted(root.iterdir()) if p.is_dir() and (p / "__init__.py").exists()]


def _has_any(path: Path, names: Iterable[str]) -> bool:
    existing = {item.name for item in path.iterdir() if item.is_file()}
    return any(name in existing for name in names)


def _classify(path: Path, layer: str, *, has_model: bool, has_builder: bool, has_runner: bool, has_schemas: bool, has_basilisk: bool) -> tuple[str, str, str]:
    name = path.name
    rel = str(path)
    if "_backup_unused" in rel or name.startswith("_backup") or name == "__pycache__":
        return "demo_only", "do_not_expose", "backup/unused source path"
    if layer in {"components", "subsystems", "integrated", "whole_spacecraft"} and (has_model or has_builder or has_schemas):
        if has_basilisk:
            return "source_native_candidate", "adapter_candidate_with_optional_basilisk", "has source model/builder/schema and Basilisk-facing files"
        return "source_native_candidate", "adapter_candidate", "has source model/builder/schema"
    if has_runner:
        return "legacy_runner_candidate", "legacy_wrapper_only", "runner present but no clear model/builder/schema"
    return "support_module", "internal_support_only", "support package, not a direct Agent capability boundary"




def _apply_coverage_policy(module: str, classification: str, exposure: str, notes: str) -> tuple[str, str, str]:
    """Apply C1 coverage-completion policy to an inventoried source module."""

    if module in EXCLUDED_SOURCE_MODULES:
        return (
            "excluded_by_policy",
            "exclude_from_agent_coverage",
            notes + "; C1 excludes this ADCS granular module from standalone Agent coverage",
        )
    if module in MODIFIER_SOURCE_MODULES:
        return (
            "modifier_support",
            "modifier_layer_only",
            notes + "; C1 treats faults/degradation as TaskSpec modifiers, not runnable capabilities",
        )
    if module in FOCUSED_ADCS_SOURCE_MODULES:
        return classification, exposure, notes + "; C1 keeps this ADCS module in focused coverage scope"
    return classification, exposure, notes


def scan_source_tree(repo_root: str | Path) -> list[SourceModuleRecord]:
    """Scan original source packages that may contain simulation capability."""

    repo = Path(repo_root)
    src_root = repo / "src"
    records: list[SourceModuleRecord] = []
    for layer in ("components", "subsystems"):
        for path in _package_dirs(src_root, layer):
            files = {item.name for item in path.iterdir() if item.is_file()}
            has_model = "model.py" in files
            has_builder = "builder.py" in files
            has_runner = "runner.py" in files
            has_schemas = "schemas.py" in files
            has_basilisk = any(name.startswith("basilisk") and name.endswith(".py") for name in files)
            has_faults = "faults.py" in files or (src_root / layer / path.name / "faults").exists()
            has_degradation = "degradation.py" in files or (src_root / layer / path.name / "degradation").exists()
            classification, exposure, notes = _classify(path, layer, has_model=has_model, has_builder=has_builder, has_runner=has_runner, has_schemas=has_schemas, has_basilisk=has_basilisk)
            module_name = _module_name(path, src_root)
            classification, exposure, notes = _apply_coverage_policy(module_name, classification, exposure, notes)
            records.append(SourceModuleRecord(
                path=str(path.relative_to(repo)),
                module=module_name,
                layer=layer[:-1],
                name=path.name,
                has_model=has_model,
                has_builder=has_builder,
                has_runner=has_runner,
                has_schemas=has_schemas,
                has_basilisk=has_basilisk,
                has_faults=has_faults,
                has_degradation=has_degradation,
                classification=classification,
                recommended_exposure=exposure,
                notes=notes,
            ))

    integrated_root = src_root / "integrated"
    if integrated_root.exists():
        for path in sorted(integrated_root.rglob("__init__.py")):
            pkg = path.parent
            if pkg == integrated_root:
                continue
            files = {item.name for item in pkg.iterdir() if item.is_file()}
            if not ({"model.py", "builder.py", "runner.py", "schemas.py", "harness.py"} & files):
                continue
            has_model = "model.py" in files
            has_builder = "builder.py" in files
            has_runner = "runner.py" in files
            has_schemas = "schemas.py" in files
            has_basilisk = any(name.startswith("basilisk") and name.endswith(".py") for name in files)
            classification, exposure, notes = _classify(pkg, "integrated", has_model=has_model, has_builder=has_builder, has_runner=has_runner, has_schemas=has_schemas, has_basilisk=has_basilisk)
            module_name = _module_name(pkg, src_root)
            classification, exposure, notes = _apply_coverage_policy(module_name, classification, exposure, notes)
            records.append(SourceModuleRecord(
                path=str(pkg.relative_to(repo)),
                module=module_name,
                layer="integrated",
                name=pkg.name,
                has_model=has_model,
                has_builder=has_builder,
                has_runner=has_runner,
                has_schemas=has_schemas,
                has_basilisk=has_basilisk,
                has_faults=False,
                has_degradation=False,
                classification=classification,
                recommended_exposure=exposure,
                notes=notes,
            ))

    ws = src_root / "whole_spacecraft"
    if ws.exists() and (ws / "__init__.py").exists():
        files = {item.name for item in ws.iterdir() if item.is_file()}
        has_model = "model.py" in files
        has_builder = "builder.py" in files
        has_runner = "runner.py" in files
        has_schemas = "schemas.py" in files
        has_basilisk = any(name.startswith("basilisk") and name.endswith(".py") for name in files)
        classification, exposure, notes = _classify(ws, "whole_spacecraft", has_model=has_model, has_builder=has_builder, has_runner=has_runner, has_schemas=has_schemas, has_basilisk=has_basilisk)
        classification, exposure, notes = _apply_coverage_policy("whole_spacecraft", classification, exposure, notes)
        records.append(SourceModuleRecord(
            path=str(ws.relative_to(repo)),
            module="whole_spacecraft",
            layer="whole_spacecraft",
            name="whole_spacecraft",
            has_model=has_model,
            has_builder=has_builder,
            has_runner=has_runner,
            has_schemas=has_schemas,
            has_basilisk=has_basilisk,
            has_faults="fault_campaign.py" in files or "fault_injector.py" in files,
            has_degradation="degradation_campaign.py" in files or "degradation_config.py" in files,
            classification=classification,
            recommended_exposure=exposure,
            notes=notes,
        ))
    return records


def _load_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required for source inventory") from exc
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def capability_source_bindings(repo_root: str | Path) -> list[dict[str, Any]]:
    """Return capability-to-source binding rows from root contracts."""

    repo = Path(repo_root)
    rows: list[dict[str, Any]] = []
    for path in sorted((repo / "capabilities").glob("*.y*ml")):
        if path.name.startswith("_"):
            continue
        data = _load_yaml(path)
        cid = data.get("capability_id")
        if not isinstance(cid, str) or not cid.strip():
            continue
        binding = source_binding_payload(data)
        rows.append({
            "capability_id": cid,
            "level": data.get("level"),
            "target": data.get("target"),
            "domain": data.get("domain"),
            "trust_level": data.get("trust_level"),
            "adapter": ((data.get("adapter") or {}).get("class_path") if isinstance(data.get("adapter"), Mapping) else None),
            "source_binding": binding,
        })
    return rows


def build_coverage_index(repo_root: str | Path) -> dict[str, Any]:
    """Build machine-readable S1 coverage index."""

    records = [record.to_dict() for record in scan_source_tree(repo_root)]
    capabilities = capability_source_bindings(repo_root)
    exposed_modules: set[str] = set()
    for cap in capabilities:
        binding = cap.get("source_binding") if isinstance(cap.get("source_binding"), Mapping) else {}
        for module in binding.get("source_modules") or []:
            if isinstance(module, str):
                exposed_modules.add(module)
    candidate_modules = [r["module"] for r in records if r["recommended_exposure"] in {"adapter_candidate", "adapter_candidate_with_optional_basilisk", "legacy_wrapper_only"}]
    covered_candidates = [m for m in candidate_modules if any(m == e or e.startswith(m + ".") for e in exposed_modules)]
    uncovered_candidates = [m for m in candidate_modules if m not in covered_candidates]
    mode_counts: dict[str, int] = {}
    for cap in capabilities:
        mode = str((cap.get("source_binding") or {}).get("mode")) if isinstance(cap.get("source_binding"), Mapping) else "unknown"
        mode_counts[mode] = mode_counts.get(mode, 0) + 1
    return {
        "schema_version": COVERAGE_INDEX_SCHEMA_VERSION,
        "source_inventory_schema_version": SOURCE_INVENTORY_SCHEMA_VERSION,
        "summary": {
            "source_module_count": len(records),
            "agent_capability_count": len(capabilities),
            "source_native_capability_count": mode_counts.get("source_native", 0),
            "synthetic_capability_count": mode_counts.get("synthetic", 0),
            "legacy_runner_capability_count": mode_counts.get("legacy_runner", 0),
            "demo_only_capability_count": mode_counts.get("demo_only", 0),
            "route_b_model_library_capability_count": mode_counts.get("route_b_model_library", 0),
            "basilisk_native_capability_count": mode_counts.get("basilisk_native", 0),
            "candidate_source_module_count": len(candidate_modules),
            "covered_candidate_source_module_count": len(covered_candidates),
            "uncovered_candidate_source_module_count": len(uncovered_candidates),
        },
        "source_modules": records,
        "capabilities": capabilities,
        "uncovered_source_candidates": uncovered_candidates,
        "coverage_policy": {
            "schema_version": "c1.source_coverage_policy.v2",
            "focused_adcs_modules": sorted(FOCUSED_ADCS_SOURCE_MODULES),
            "excluded_modules": sorted(EXCLUDED_SOURCE_MODULES),
            "modifier_modules": sorted(MODIFIER_SOURCE_MODULES),
            "fault_degradation_policy": "TaskSpec modifiers, not standalone runnable capabilities",
        },
    }


def markdown_inventory(index: Mapping[str, Any]) -> str:
    summary = index.get("summary") if isinstance(index.get("summary"), Mapping) else {}
    records = index.get("source_modules") if isinstance(index.get("source_modules"), list) else []
    lines = [
        "# S1 Source Capability Inventory",
        "",
        "本清单盘点原始 `src` 中已有的仿真相关模块，用于决定哪些能力应通过 source-native adapter 暴露给 Agent。它不提升或改写仿真物理精度。",
        "",
        "## Summary",
        "",
        f"- Source modules inventoried: {summary.get('source_module_count')}",
        f"- Agent capabilities: {summary.get('agent_capability_count')}",
        f"- Source-native capabilities: {summary.get('source_native_capability_count')}",
        f"- Synthetic capabilities: {summary.get('synthetic_capability_count')}",
        f"- Candidate source modules not yet covered: {summary.get('uncovered_candidate_source_module_count')}",
        "",
        "## Inventory table",
        "",
        "| Layer | Module | Model | Builder | Runner | Schemas | Basilisk | Classification | Recommended exposure |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in records:
        lines.append(
            f"| {row.get('layer')} | `{row.get('module')}` | {bool(row.get('has_model'))} | {bool(row.get('has_builder'))} | {bool(row.get('has_runner'))} | {bool(row.get('has_schemas'))} | {bool(row.get('has_basilisk'))} | {row.get('classification')} | {row.get('recommended_exposure')} |"
        )
    lines.extend([
        "",
        "## Exposure rules",
        "",
        "- `adapter_candidate`: 有 model / builder / schema，可优先做 source-native adapter。",
        "- `adapter_candidate_with_optional_basilisk`: 有 Basilisk 相关文件；可以封装，但必须明确运行环境依赖。",
        "- `legacy_wrapper_only`: 只有 runner 或主要靠 runner；除非没有模型级 API，否则不要作为生产 Agent 边界。",
        "- `do_not_expose`: backup / unused / demo-only 路径，不暴露给普通用户 Agent。",
    ])
    return "\n".join(lines) + "\n"


def markdown_coverage_matrix(index: Mapping[str, Any]) -> str:
    capabilities = index.get("capabilities") if isinstance(index.get("capabilities"), list) else []
    uncovered = index.get("uncovered_source_candidates") if isinstance(index.get("uncovered_source_candidates"), list) else []
    lines = [
        "# S1 Source-to-Capability Coverage Matrix",
        "",
        "本矩阵记录当前 Agent capability 与原始 `src` 模块的绑定关系。`synthetic` 不表示错误，只表示该 adapter 是 Agent-facing deterministic boundary，而不是直接调用原始模型类。",
        "",
        "## Capability bindings",
        "",
        "| Capability | Level | Target | Binding mode | Primary source | Source modules | Adapter |",
        "|---|---|---|---|---|---|---|",
    ]
    for cap in capabilities:
        binding = cap.get("source_binding") if isinstance(cap.get("source_binding"), Mapping) else {}
        modules = binding.get("source_modules") or []
        module_text = ", ".join(f"`{m}`" for m in modules) if modules else "-"
        lines.append(
            f"| `{cap.get('capability_id')}` | {cap.get('level')} | {cap.get('target')} | {binding.get('mode')} | `{binding.get('primary_module')}` | {module_text} | `{cap.get('adapter')}` |"
        )
    lines.extend([
        "",
        "## Uncovered source candidates",
        "",
    ])
    if uncovered:
        for module in uncovered:
            lines.append(f"- `{module}`")
    else:
        lines.append("- None")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "- S3/S4 应优先处理 uncovered candidates 中用户价值高、模型级 API 明确的模块。",
        "- 对只有 runner 的模块，优先判断能否下探到 model/builder；否则只允许 legacy wrapper，并在 manifest 中标记。",
        "- 对 synthetic capability，后续只有在原 src 存在对应稳定 API 时才迁移；不为了“source-native”而降低脚本稳定性。",
    ])
    return "\n".join(lines) + "\n"


def write_inventory_artifacts(repo_root: str | Path) -> dict[str, Path]:
    """Write S1 inventory docs and coverage index YAML."""

    try:
        import yaml  # type: ignore
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PyYAML is required for source inventory") from exc
    repo = Path(repo_root)
    index = build_coverage_index(repo)
    docs = repo / "docs"
    caps = repo / "capabilities"
    pkg_caps = repo / "src" / "sat_sim" / "capabilities"
    docs.mkdir(exist_ok=True)
    caps.mkdir(exist_ok=True)
    pkg_caps.mkdir(parents=True, exist_ok=True)
    paths = {
        "inventory_doc": docs / "src_capability_inventory.md",
        "coverage_doc": docs / "src_to_capability_coverage_matrix.md",
        "coverage_index": caps / "_coverage_index.yaml",
        "package_coverage_index": pkg_caps / "_coverage_index.yaml",
    }
    paths["inventory_doc"].write_text(markdown_inventory(index), encoding="utf-8")
    paths["coverage_doc"].write_text(markdown_coverage_matrix(index), encoding="utf-8")
    yaml_text = yaml.safe_dump(index, sort_keys=False, allow_unicode=True)
    paths["coverage_index"].write_text(yaml_text, encoding="utf-8")
    paths["package_coverage_index"].write_text(yaml_text, encoding="utf-8")
    return paths


__all__ = [
    "SOURCE_INVENTORY_SCHEMA_VERSION",
    "COVERAGE_INDEX_SCHEMA_VERSION",
    "SourceModuleRecord",
    "scan_source_tree",
    "capability_source_bindings",
    "build_coverage_index",
    "write_inventory_artifacts",
]
