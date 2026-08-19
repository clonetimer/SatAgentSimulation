"""Optional adapters for mature Agent frameworks.

The simulation core must remain deterministic and testable without network
access. This module therefore exposes small adapter factories that production
applications can call when the relevant framework package is installed.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .agent import agent_context_payload, repair_hints_from_issues
from .task_compiler import compile_task_spec
from .task_spec import load_task_spec
from .task_validator import validate_task_spec


def framework_recommendation() -> dict[str, Any]:
    """Return the P4 framework recommendation used by docs and Agent context."""

    return {
        "default_runtime": "sat_sim.agent_orchestrator deterministic graph",
        "production_llm_adapter": "OpenAI Agents SDK or LangGraph",
        "why": [
            "Keep TaskSpec validation/compilation/running as trusted tools, not free-form generated code.",
            "Use framework tracing/state only around the generation loop; do not move simulation physics into prompts.",
            "Keep template backend for CI and offline regression tests.",
        ],
        "tool_boundary": ["catalog", "validate", "explain_errors", "compile", "run", "export_script"],
    }


def build_openai_agents_tools() -> list[Any]:
    """Build OpenAI Agents SDK function tools when ``agents`` is installed.

    The returned tools are intentionally narrow: they expose TaskSpec-oriented
    operations and avoid arbitrary shell/Python execution.
    """

    try:
        from agents import function_tool  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        raise ImportError("OpenAI Agents SDK is not installed; install the package that provides 'agents'.") from exc

    @function_tool
    def sat_sim_agent_context(examples_dir: str = "examples") -> str:
        """Return compact TaskSpec generation context, examples, and target catalog."""
        return json.dumps(agent_context_payload(examples_dir), ensure_ascii=False)

    @function_tool
    def sat_sim_validate(spec_path: str) -> str:
        """Validate a TaskSpec file and return JSON validation result."""
        doc = load_task_spec(Path(spec_path))
        return json.dumps(validate_task_spec(doc.data).to_dict(), ensure_ascii=False)

    @function_tool
    def sat_sim_compile(spec_path: str) -> str:
        """Compile a TaskSpec file and return JSON compiled plan."""
        doc = load_task_spec(Path(spec_path))
        validation = validate_task_spec(doc.data)
        validation.raise_for_errors()
        return json.dumps(compile_task_spec(doc.data, validate=False).to_dict(), ensure_ascii=False)

    @function_tool
    def sat_sim_explain_errors(spec_path: str) -> str:
        """Validate a TaskSpec file and return Agent-oriented repair hints."""
        doc = load_task_spec(Path(spec_path))
        result = validate_task_spec(doc.data)
        return json.dumps({"validation": result.to_dict(), "repair_hints": [h.to_dict() for h in repair_hints_from_issues(result)]}, ensure_ascii=False)

    return [sat_sim_agent_context, sat_sim_validate, sat_sim_compile, sat_sim_explain_errors]


def build_langgraph_node_functions() -> dict[str, Any]:
    """Return pure node functions compatible with a LangGraph-style StateGraph.

    The function does not import LangGraph. Applications can register these
    nodes in their own graph and keep state shape under their control.
    """

    def load_context_node(state: Mapping[str, Any]) -> dict[str, Any]:
        return {"context": agent_context_payload(state.get("examples_dir", "examples"))}

    def validate_node(state: Mapping[str, Any]) -> dict[str, Any]:
        spec = state.get("task_spec")
        if not isinstance(spec, Mapping):
            return {"validation": {"ok": False, "errors": [{"message": "state.task_spec must be an object"}]}}
        return {"validation": validate_task_spec(spec).to_dict()}

    def compile_node(state: Mapping[str, Any]) -> dict[str, Any]:
        spec = state.get("task_spec")
        if not isinstance(spec, Mapping):
            return {"compiled": None, "compile_error": "state.task_spec must be an object"}
        compiled = compile_task_spec(spec, validate=True)
        return {"compiled": compiled.to_dict()}

    return {"load_context": load_context_node, "validate": validate_node, "compile": compile_node}


__all__ = ["build_langgraph_node_functions", "build_openai_agents_tools", "framework_recommendation"]
