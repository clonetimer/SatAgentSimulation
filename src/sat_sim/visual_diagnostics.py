"""Map Visual Composer failures back to graph nodes and ports.

The mapper is intentionally conservative: it does not reinterpret runtime
physics or accept executable input.  It only correlates already-produced error
text/reason codes with the submitted visual graph, assembly graph and TaskSpec
so the workbench can highlight likely fault locations.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Mapping, Sequence

VISUAL_DIAGNOSTICS_SCHEMA_VERSION = "sat-sim.visual-diagnostics.v1"
_REASON_RE = re.compile(r"\b([A-Z][A-Z0-9_]{3,})\b")
_PATH_RE = re.compile(r"\$\.?[A-Za-z0-9_\.\[\]-]+")


@dataclass(frozen=True)
class DiagnosticTarget:
    kind: str
    node_id: str | None = None
    edge_id: str | None = None
    port_id: str | None = None
    label: str = ""
    severity: str = "error"
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in (None, "")}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _add_target(targets: list[DiagnosticTarget], target: DiagnosticTarget) -> None:
    key = (target.kind, target.node_id, target.edge_id, target.port_id)
    if any((item.kind, item.node_id, item.edge_id, item.port_id) == key for item in targets):
        return
    targets.append(target)


def _task_capability_id(task_spec: Mapping[str, Any] | None) -> str:
    task = _mapping(task_spec)
    return str(task.get("capability_id") or _mapping(task.get("model")).get("capability_id") or "").strip()


def _reason_code(reason_code: str | None, error_text: str) -> str:
    if reason_code:
        return str(reason_code).strip()
    candidates = _REASON_RE.findall(error_text or "")
    preferred = [item for item in candidates if "_" in item]
    return preferred[0] if preferred else "VISUAL_RUNTIME_FAILURE"


def _diagnose_flow(
    text: str,
    reason_code: str,
    graph: Mapping[str, Any],
    task_spec: Mapping[str, Any] | None,
) -> list[DiagnosticTarget]:
    targets: list[DiagnosticTarget] = []
    nodes = [item for item in _sequence(graph.get("nodes")) if isinstance(item, Mapping)]
    capability_id = _task_capability_id(task_spec)

    def by_type(node_type: str, reason: str) -> None:
        for node in nodes:
            if str(node.get("type") or "") == node_type:
                _add_target(targets, DiagnosticTarget(kind="node", node_id=str(node.get("id") or ""), label=node_type, reason=reason))
                break

    for node in nodes:
        node_capability = str(node.get("capabilityId") or node.get("capability_id") or "")
        if node_capability and node_capability in text:
            _add_target(targets, DiagnosticTarget(kind="node", node_id=str(node.get("id") or ""), label=node_capability, reason="错误信息命中了该 Capability"))

    lower = text.lower()
    paths = _PATH_RE.findall(text)
    joined_paths = " ".join(paths).lower()
    if any(token in joined_paths for token in ("simulation", "parameters", "spacecraft", "metadata")):
        by_type("config", "错误路径指向运行/模型参数")
    if "event" in joined_paths or "fault" in lower or "degradation" in lower:
        by_type("effects", "错误与事件/故障配置相关")
    if "output" in joined_paths or "qoi" in lower or "telemetry" in lower:
        by_type("outputs", "错误与输出/QoI 配置相关")
    if "script" in lower or "python" in lower or "codegen" in lower or "export" in reason_code.lower():
        by_type("code", "错误发生在代码生成/脚本阶段")
    if any(token in lower for token in ("basilisk", "runtime", "execute", "execution", "worker", "timeout")) or reason_code.startswith("RUN_"):
        by_type("run", "错误发生在运行时/执行器阶段")
    if "capability" in lower or (capability_id and capability_id in text):
        by_type("model", "错误涉及执行能力")
    if not targets:
        by_type("run", "未识别到更具体位置，先定位到运行器")
    return targets


def _diagnose_assembly(
    text: str,
    reason_code: str,
    graph: Mapping[str, Any],
    task_spec: Mapping[str, Any] | None,
) -> list[DiagnosticTarget]:
    targets: list[DiagnosticTarget] = []
    nodes = [item for item in _sequence(graph.get("nodes")) if isinstance(item, Mapping)]
    edges = [item for item in _sequence(graph.get("edges")) if isinstance(item, Mapping)]
    lower = text.lower()

    node_by_id = {str(item.get("id") or ""): item for item in nodes}
    parent = next((item for item in nodes if str(item.get("moduleAlias") or item.get("module_alias") or "") == "assembly"), nodes[0] if nodes else None)

    for node in nodes:
        node_id = str(node.get("id") or "")
        alias = str(node.get("moduleAlias") or node.get("module_alias") or "")
        capability_id = str(node.get("capabilityId") or node.get("capability_id") or "")
        if capability_id and capability_id in text:
            _add_target(targets, DiagnosticTarget(kind="node", node_id=node_id, label=alias or capability_id, reason="错误信息命中了当前模块 Capability"))
        elif alias and re.search(rf"(?<![A-Za-z0-9_]){re.escape(alias)}(?![A-Za-z0-9_])", text, re.IGNORECASE):
            _add_target(targets, DiagnosticTarget(kind="node", node_id=node_id, label=alias, reason="错误信息命中了模块 alias"))

    for edge in edges:
        edge_id = str(edge.get("id") or "")
        binding_id = str(edge.get("bindingId") or edge.get("binding_id") or "")
        source_port = str(edge.get("sourcePort") or edge.get("source_port") or "")
        target_port = str(edge.get("targetPort") or edge.get("target_port") or "")
        hit = (binding_id and binding_id in text) or (source_port and source_port in text) or (target_port and target_port in text)
        if not hit:
            continue
        _add_target(targets, DiagnosticTarget(kind="edge", edge_id=edge_id, label=binding_id or edge_id, reason="错误信息命中了该 signal binding"))
        for node_key, port_id in (("source", source_port), ("target", target_port)):
            node_id = str(edge.get(node_key) or "")
            if node_id in node_by_id:
                _add_target(targets, DiagnosticTarget(kind="port", node_id=node_id, port_id=port_id, label=port_id, reason="错误信息命中了该端口/绑定"))

    if "MODULE_SELECTION" in reason_code or "module selection" in lower:
        visual = _mapping(_mapping(task_spec).get("metadata")).get("visual_assembly")
        selections = _mapping(_mapping(visual).get("module_selections"))
        for node in nodes:
            alias = str(node.get("moduleAlias") or node.get("module_alias") or "")
            if alias in selections:
                selected = selections.get(alias)
                node_capability = node.get("capabilityId") or node.get("capability_id")
                if (selected or None) != (node_capability or None):
                    _add_target(targets, DiagnosticTarget(kind="node", node_id=str(node.get("id") or ""), label=alias, reason="TaskSpec 与画布模块选择不一致"))

    if any(token in reason_code for token in ("BINDING", "PORT", "SIGNAL", "FANIN", "FANOUT", "LOOP")) and not any(item.kind in {"edge", "port"} for item in targets):
        # Structural assembly errors without a parseable binding are best rooted
        # at the parent runtime owner rather than guessing a child module.
        if parent:
            _add_target(targets, DiagnosticTarget(kind="node", node_id=str(parent.get("id") or ""), label="assembly", reason="装配/端口合同校验失败，未能进一步解析具体 binding"))

    if any(token in lower for token in ("basilisk", "runtime", "execute", "execution", "worker", "timeout")) or reason_code.startswith("RUN_"):
        # Runtime failures may stem from a selected replacement. Highlight the
        # parent owner plus any explicitly mentioned child already found.
        if parent:
            _add_target(targets, DiagnosticTarget(kind="node", node_id=str(parent.get("id") or ""), label="runtime owner", reason="父 Capability 拥有实际运行时"))

    if not targets and parent:
        _add_target(targets, DiagnosticTarget(kind="node", node_id=str(parent.get("id") or ""), label="runtime owner", reason="未识别到更具体位置，先定位到父运行节点"))
    return targets


def diagnose_visual_failure(
    *,
    error_text: str,
    reason_code: str | None = None,
    task_spec: Mapping[str, Any] | None = None,
    visual_graph: Mapping[str, Any] | None = None,
    assembly_graph: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return conservative graph locations correlated with a failure."""

    text = str(error_text or "").strip()
    code = _reason_code(reason_code, text)
    mode = "assembly" if assembly_graph is not None else "flow" if visual_graph is not None else "unknown"
    if assembly_graph is not None:
        targets = _diagnose_assembly(text, code, assembly_graph, task_spec)
    elif visual_graph is not None:
        targets = _diagnose_flow(text, code, visual_graph, task_spec)
    else:
        targets = []
    labels = [item.label for item in targets if item.label][:4]
    return {
        "schema_version": VISUAL_DIAGNOSTICS_SCHEMA_VERSION,
        "ok": bool(targets),
        "mode": mode,
        "reason_code": code,
        "summary": f"已定位到 {len(targets)} 个图形目标" + (f"：{'、'.join(labels)}" if labels else ""),
        "targets": [item.to_dict() for item in targets],
        "arbitrary_code_inspected": False,
    }
