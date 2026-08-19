"""Command-line interface for TaskSpec-driven SatSim workflows."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping

from .task_compiler import compile_task_spec
from .unified_execution import execute_compiled_task
from .task_spec import TaskSpecError, load_task_spec, write_json
from .task_validator import format_issues, validate_task_spec


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        doc = load_task_spec(args.spec)
        result = validate_task_spec(doc.data, use_json_schema=not args.no_schema)
    except Exception as exc:
        _print_json({"ok": False, "errors": [{"message": str(exc)}], "warnings": []})
        return 2
    if args.json:
        _print_json(result.to_dict())
    else:
        status = "OK" if result.ok else "FAILED"
        print(f"TaskSpec validation: {status}")
        if result.issues:
            print(format_issues(result.issues))
    return 0 if result.ok else 1


def cmd_migrate(args: argparse.Namespace) -> int:
    """Migrate a legacy TaskSpec to Canonical TaskSpec 1.0."""

    try:
        from .task_models import CANONICAL_TASK_SPEC_VERSION, is_canonical_task_spec, migrate_legacy_task_spec, canonicalize_task_spec

        doc = load_task_spec(args.spec)
        if is_canonical_task_spec(doc.data):
            canonical = canonicalize_task_spec(doc.data)
            report = {
                "source_version": CANONICAL_TASK_SPEC_VERSION,
                "target_version": CANONICAL_TASK_SPEC_VERSION,
                "notices": [],
                "already_canonical": True,
            }
        else:
            migration = migrate_legacy_task_spec(doc.data)
            canonical = migration.canonical
            report = {**migration.to_dict(), "already_canonical": False}
            report.pop("canonical", None)

        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.suffix.lower() == ".json":
            write_json(output, canonical)
        else:
            import yaml

            output.write_text(yaml.safe_dump(canonical, sort_keys=False, allow_unicode=True), encoding="utf-8")
        if args.report:
            write_json(args.report, report)
        _print_json({
            "ok": True,
            "output": str(output),
            "report_output": str(args.report) if args.report else None,
            "migration": report,
        })
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def _has_capability_selection(spec: Mapping[str, Any]) -> bool:
    if isinstance(spec.get("capability_id"), str) and str(spec.get("capability_id")).strip():
        return True
    model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
    return isinstance(model.get("capability_id"), str) and bool(str(model.get("capability_id")).strip())


def _plan_capability_spec(spec: Mapping[str, Any]):
    from .execution_planner import plan_task_spec

    planning = plan_task_spec(spec)
    if not planning.ok:
        raise TaskSpecError(json.dumps(planning.validation.to_dict(), ensure_ascii=False))
    return planning


def cmd_resolve(args: argparse.Namespace) -> int:
    try:
        from .execution_planner import plan_task_spec
        import yaml

        doc = load_task_spec(args.spec)
        planning = plan_task_spec(doc.data)
        payload = planning.to_dict()
        if planning.resolved_spec is not None and args.output:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            resolved = planning.resolved_spec.model_dump(mode="json")
            if output.suffix.lower() == ".json":
                write_json(output, resolved)
            else:
                output.write_text(yaml.safe_dump(resolved, sort_keys=False, allow_unicode=True), encoding="utf-8")
        if args.report:
            write_json(args.report, planning.validation.to_dict())
        _print_json(payload)
        return 0 if planning.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_plan(args: argparse.Namespace) -> int:
    try:
        from .execution_planner import plan_task_spec
        import yaml

        doc = load_task_spec(args.spec)
        planning = plan_task_spec(doc.data)
        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        files: dict[str, str] = {}
        files["plan_validation"] = str(write_json(output_dir / "plan_validation.json", planning.validation.to_dict()))
        if planning.resolved_spec is not None:
            resolved = planning.resolved_spec.model_dump(mode="json")
            files["resolved_spec_json"] = str(write_json(output_dir / "resolved_spec.json", resolved))
            resolved_yaml = output_dir / "resolved_spec.yaml"
            resolved_yaml.write_text(yaml.safe_dump(resolved, sort_keys=False, allow_unicode=True), encoding="utf-8")
            files["resolved_spec_yaml"] = str(resolved_yaml)
        if planning.execution_plan is not None:
            files["execution_plan"] = str(write_json(output_dir / "execution_plan.json", planning.execution_plan.model_dump(mode="json")))
        _print_json({"ok": planning.ok, "planning": planning.to_dict(), "files": files})
        return 0 if planning.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_compile(args: argparse.Namespace) -> int:
    try:
        doc = load_task_spec(args.spec)
        planning = _plan_capability_spec(doc.data) if _has_capability_selection(doc.data) else None
        compiled = compile_task_spec(doc.data, validate=not args.no_validate)
        if planning is not None:
            from .execution_planner import attach_plan_metadata
            compiled = attach_plan_metadata(compiled, planning)
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2
    payload = compiled.to_dict()
    if args.output:
        write_json(args.output, payload)
    _print_json({"ok": True, "compiled": payload})
    return 0


def cmd_expand(args: argparse.Namespace) -> int:
    try:
        from .campaign import expand_campaign_spec, write_campaign_plan

        doc = load_task_spec(args.spec)
        result = validate_task_spec(doc.data, use_json_schema=not args.no_schema)
        result.raise_for_errors()
        plan = expand_campaign_spec(doc.data, output_root=args.output_root)
        payload = plan.to_dict()
        if args.output:
            write_campaign_plan(args.output, plan)
        _print_json({"ok": True, "plan": payload})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_agent_context(args: argparse.Namespace) -> int:
    try:
        from .agent import agent_context_payload

        _print_json({"ok": True, "agent_context": agent_context_payload(args.examples_dir)})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_explain_errors(args: argparse.Namespace) -> int:
    try:
        from .agent import repair_hints_from_issues

        doc = load_task_spec(args.spec)
        result = validate_task_spec(doc.data, use_json_schema=not args.no_schema)
        hints = [hint.to_dict() for hint in repair_hints_from_issues(result)]
        _print_json({"ok": result.ok, "validation": result.to_dict(), "repair_hints": hints})
        return 0 if result.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def _read_request_text(args: argparse.Namespace) -> str:
    if getattr(args, "request_file", None):
        return Path(args.request_file).read_text(encoding="utf-8").strip()
    if getattr(args, "request", None):
        return str(args.request).strip()
    raise ValueError("request text is required; pass REQUEST or --request-file")


def cmd_generate(args: argparse.Namespace) -> int:
    try:
        from .agent_orchestrator import generate_task_from_text

        text = _read_request_text(args)
        result = generate_task_from_text(
            text,
            output_dir=args.output_dir,
            examples_dir=args.examples_dir,
            backend=args.backend,
            auto_run=args.run,
            output_root=args.output_root,
            task_id=args.task_id,
            max_repair_attempts=args.max_repairs,
            dry_run=args.dry_run,
        )
        payload = result.to_dict()
        if args.json:
            _print_json(payload)
        else:
            print(f"Agent generation: {'OK' if result.ok else 'FAILED'}")
            print(f"Generated TaskSpec: {payload['files'].get('task_spec_yaml') or payload['files'].get('generated_task_yaml')}")
            if payload.get("compiled"):
                print(f"Compiled runner: {payload['compiled'].get('runner')}")
            if payload.get("run_result"):
                print(f"Run summary: {json.dumps(payload['run_result'].get('summary'), ensure_ascii=False)}")
        return 0 if result.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_export_script(args: argparse.Namespace) -> int:
    try:
        from .script_exporter import export_runner_script

        result = export_runner_script(args.spec, args.output, kind=args.kind, output_root=args.output_root)
        _print_json({"ok": True, "script": result.to_dict()})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_eval_agent(args: argparse.Namespace) -> int:
    try:
        from .eval_runner import run_agent_evals

        report = run_agent_evals(
            args.evals_dir,
            output_dir=args.output_dir,
            examples_dir=args.examples_dir,
            backend=args.backend,
            run=args.run,
        )
        _print_json({"ok": report.ok, "report": report.to_dict()})
        return 0 if report.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_agent_frameworks(args: argparse.Namespace) -> int:
    try:
        from .agent_frameworks import framework_recommendation

        _print_json({"ok": True, "frameworks": framework_recommendation()})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2



def cmd_agent_prompt(args: argparse.Namespace) -> int:
    try:
        from .capability_agent import build_capability_agent_prompt, capability_agent_context_payload

        allowed = tuple(args.capability) if getattr(args, "capability", None) else None
        if args.context_json:
            payload = capability_agent_context_payload(allowed_capabilities=allowed) if allowed else capability_agent_context_payload()
            if args.output:
                write_json(args.output, payload)
            _print_json({"ok": True, "capability_context": payload})
            return 0
        text = _read_request_text(args)
        compact = not getattr(args, "full_context", False)
        prompt = build_capability_agent_prompt(text, allowed_capabilities=allowed, compact=compact) if allowed else build_capability_agent_prompt(text, compact=compact)
        if args.output:
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(prompt, encoding="utf-8")
        if args.json:
            _print_json({"ok": True, "prompt": prompt, "output": str(args.output) if args.output else None})
        else:
            print(prompt)
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_generate_script(args: argparse.Namespace) -> int:
    try:
        from .agent_facade import AgentFacadeRequest, run_agent
        from .capability_agent import DEFAULT_ALLOWED_CAPABILITIES

        text = _read_request_text(args)
        allowed = tuple(args.capability) if getattr(args, "capability", None) else DEFAULT_ALLOWED_CAPABILITIES
        result = run_agent(AgentFacadeRequest(
            request=text,
            output_dir=args.output_dir,
            examples_dir=args.examples_dir,
            backend=args.backend,
            draft_spec_path=args.draft_spec,
            allowed_capabilities=tuple(allowed),
            task_id=args.task_id,
            output_root=args.output_root,
            script_output=args.script_output,
            max_repair_attempts=args.max_repairs,
            auto_run=args.run,
            dry_run=args.dry_run,
            model_command=getattr(args, "model_command", None),
            model_name=getattr(args, "model", None),
            model_base_url=getattr(args, "model_base_url", None),
            model_api_key_env=getattr(args, "model_api_key_env", "OPENAI_API_KEY"),
            model_timeout_s=getattr(args, "model_timeout_s", 60.0),
            model_temperature=getattr(args, "temperature", None),
            model_max_output_tokens=getattr(args, "max_output_tokens", None),
            model_structured_output=getattr(args, "structured_output", "text"),
        ))
        payload = result.to_dict()
        if args.json:
            _print_json(payload)
        else:
            print(f"Agent script generation: {'OK' if result.ok else 'FAILED'}")
            print(f"Generated TaskSpec: {payload['files'].get('task_spec_yaml')}")
            print(f"Generated script: {payload['files'].get('generated_script')}")
            if payload.get("compiled"):
                print(f"Capability: {payload['compiled'].get('metadata', {}).get('capability_id') or payload['task_spec'].get('model', {}).get('capability_id')}")
            if payload.get("validation") and not payload["validation"].get("ok"):
                print(json.dumps(payload["validation"], indent=2, ensure_ascii=False))
        return 0 if result.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_run(args: argparse.Namespace) -> int:
    try:
        doc = load_task_spec(args.spec)
        result = validate_task_spec(doc.data, use_json_schema=not args.no_schema)
        result.raise_for_errors()
        planning = _plan_capability_spec(doc.data) if _has_capability_selection(doc.data) else None
        compiled = compile_task_spec(doc.data, validate=False)
        if planning is not None:
            from .execution_planner import attach_plan_metadata
            compiled = attach_plan_metadata(compiled, planning)
        output_root = args.output or compiled.outputs.get("output_root")
        run_result = execute_compiled_task(compiled, task_spec=doc.data, output_root=output_root, write_dataset=not args.no_dataset)
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2
    payload = {
        "ok": True,
        "task_id": compiled.task_id,
        "task_type": compiled.task_type,
        "mode": compiled.mode,
        "summary": run_result.summary,
        "dataset": run_result.dataset.to_dict() if run_result.dataset else None,
    }
    _print_json(payload)
    return 0




def cmd_repair(args: argparse.Namespace) -> int:
    try:
        from .limited_repair import repair_task_spec_limited
        import yaml

        doc = load_task_spec(args.spec)
        report = repair_task_spec_limited(doc.data, max_repairs=args.max_repairs)
        files: dict[str, str] = {}
        if args.output and report.repaired_spec is not None:
            output = Path(args.output)
            output.parent.mkdir(parents=True, exist_ok=True)
            if output.suffix.lower() == ".json":
                write_json(output, report.repaired_spec)
            else:
                output.write_text(yaml.safe_dump(report.repaired_spec, sort_keys=False, allow_unicode=True), encoding="utf-8")
            files["repaired_spec"] = str(output)
        if args.report:
            write_json(args.report, report.to_dict())
            files["repair_report"] = str(args.report)
        _print_json({"ok": report.repaired_valid, "repair": report.to_dict(), "files": files})
        return 0 if report.repaired_valid else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_prepare_run(args: argparse.Namespace) -> int:
    try:
        from .run_bundle import prepare_run

        doc = load_task_spec(args.spec)
        prepared = prepare_run(
            doc.data,
            output_root=args.output_root,
            run_id=args.run_id,
            supersedes_run_id=args.supersedes,
        )
        _print_json({"ok": True, "prepared_run": prepared.model_dump(mode="json")})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_execute_run(args: argparse.Namespace) -> int:
    try:
        from .run_bundle import execute_prepared_run

        result = execute_prepared_run(
            args.bundle,
            expected_plan_sha256=args.plan_sha256,
            max_attempts=args.max_attempts,
            hard_timeout=args.hard_timeout,
        )
        execution_ok = result.run_record.status == "SUCCEEDED"
        validation_result = result.validation.result.value if hasattr(result.validation.result, "value") else str(result.validation.result)
        validation_ok = validation_result == "PASS"
        _print_json({
            "ok": execution_ok and validation_ok,
            "execution_ok": execution_ok,
            "validation_ok": validation_ok,
            "validation_result": validation_result,
            "result": result.model_dump(mode="json"),
        })
        return 0 if execution_ok and validation_ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_cancel_run(args: argparse.Namespace) -> int:
    try:
        from .run_bundle import request_cancel

        path = request_cancel(args.bundle, reason=args.reason)
        _print_json({"ok": True, "cancel_request": str(path)})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_verify_run(args: argparse.Namespace) -> int:
    try:
        from .run_bundle import verify_run_bundle

        result = verify_run_bundle(args.bundle)
        _print_json(result)
        return 0 if result.get("ok") else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2

def cmd_queue_worker(args: argparse.Namespace) -> int:
    try:
        from .durable_queue import DurableExecutionQueue
        from .execution_worker import ExecutionQueueWorker

        queue = DurableExecutionQueue(args.database)
        worker = ExecutionQueueWorker(
            queue,
            worker_id=args.worker_id,
            poll_interval_s=args.poll_interval,
            lease_seconds=args.lease_seconds,
        )
        completed = worker.run_forever(max_jobs=args.max_jobs, idle_timeout_s=args.idle_timeout)
        _print_json({"ok": True, "worker_id": worker.worker_id, "completed_jobs": completed, "queue": queue.health()})
        return 0
    except KeyboardInterrupt:
        _print_json({"ok": True, "stopped": "keyboard_interrupt"})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_remote_worker(args: argparse.Namespace) -> int:
    try:
        import os
        from .remote_worker import RemoteExecutionWorker, RemoteWorkerConfig

        token = os.getenv(args.token_env)
        if not token:
            raise ValueError(f"environment variable {args.token_env} is not configured")
        config = RemoteWorkerConfig(
            control_plane_url=args.control_plane,
            worker_id=args.worker_id,
            token=token,
            work_root=args.work_root,
            poll_interval_s=args.poll_interval,
            heartbeat_interval_s=args.heartbeat_interval,
            lease_seconds=args.lease_seconds,
            request_timeout_s=args.request_timeout,
            insecure_skip_tls_verify=args.insecure_skip_tls_verify,
            capabilities={
                "execution_type": "simulation",
                "basilisk": True,
                "transport": "https-bundle",
                "labels": list(args.label or []),
            },
        )
        worker = RemoteExecutionWorker(config)
        completed = worker.run_forever(max_jobs=args.max_jobs, idle_timeout_s=args.idle_timeout)
        _print_json({"ok": True, "worker_id": args.worker_id, "completed_jobs": completed})
        return 0
    except KeyboardInterrupt:
        _print_json({"ok": True, "stopped": "keyboard_interrupt"})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_token_sha256(args: argparse.Namespace) -> int:
    try:
        import getpass
        import os
        from .security import token_sha256

        token = os.getenv(args.token_env) if args.token_env else None
        if token is None and args.token:
            token = args.token
        if token is None:
            token = getpass.getpass("Token: ")
        if not token:
            raise ValueError("token is empty")
        _print_json({"ok": True, "token_sha256": token_sha256(token), "secret_values_exposed": False})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_queue_status(args: argparse.Namespace) -> int:
    try:
        from .durable_queue import DurableExecutionQueue

        queue = DurableExecutionQueue(args.database)
        payload = queue.list_jobs(limit=args.limit, offset=args.offset, states=args.state)
        _print_json({"ok": True, "queue": queue.health(), **payload, "workers": queue.list_workers()})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_provider_eval(args: argparse.Namespace) -> int:
    try:
        from .provider_eval import build_provider_readiness_matrix, run_provider_eval

        if args.readiness_matrix:
            build_provider_readiness_matrix(output_path=args.readiness_matrix)
        report = run_provider_eval(
            provider_id=args.provider_id,
            provider_kind=args.provider_kind,
            cases_path=args.cases,
            output_dir=args.output_dir,
        )
        _print_json({"ok": report.provider_execution_status != "EXECUTED" or report.failed_count == 0, "report": report.to_dict()})
        return 0 if report.provider_execution_status != "EXECUTED" or report.failed_count == 0 else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_model_providers(args: argparse.Namespace) -> int:
    try:
        from .model_providers import ModelProviderRegistry

        registry = ModelProviderRegistry(config_path=args.config)
        _print_json({"ok": True, "catalog": registry.catalog(live_probe=args.live_probe)})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_eval_capability_agent(args: argparse.Namespace) -> int:
    try:
        from .capability_agent import DEFAULT_ALLOWED_CAPABILITIES
        from .capability_agent_eval import run_capability_agent_evals

        allowed = tuple(args.capability) if getattr(args, "capability", None) else DEFAULT_ALLOWED_CAPABILITIES
        report = run_capability_agent_evals(
            args.evals_dir,
            output_dir=args.output_dir,
            examples_dir=args.examples_dir,
            backend=args.backend,
            model_command=args.model_command,
            model_name=args.model,
            model_base_url=args.model_base_url,
            model_api_key_env=args.model_api_key_env,
            model_timeout_s=args.model_timeout_s,
            model_temperature=args.temperature,
            model_max_output_tokens=args.max_output_tokens,
            model_structured_output=getattr(args, "structured_output", "text"),
            allowed_capabilities=allowed,
            run_scripts=args.run_scripts,
            script_timeout_s=args.script_timeout_s,
        )
        _print_json({"ok": report.ok, "report": report.to_dict()})
        return 0 if report.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_eval_source_grounded_agent(args: argparse.Namespace) -> int:
    try:
        from .capability_agent import DEFAULT_ALLOWED_CAPABILITIES
        from .source_grounded_eval import run_source_grounded_evals

        allowed = tuple(args.capability) if getattr(args, "capability", None) else DEFAULT_ALLOWED_CAPABILITIES
        report = run_source_grounded_evals(
            args.evals_dir,
            output_dir=args.output_dir,
            examples_dir=args.examples_dir,
            backend=args.backend,
            allowed_capabilities=allowed,
            run_scripts=args.run_scripts,
            script_timeout_s=args.script_timeout_s,
        )
        _print_json({"ok": report.ok, "report": report.to_dict()})
        return 0 if report.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_llm_backends(args: argparse.Namespace) -> int:
    _print_json({
        "ok": True,
        "backends": [
            {
                "name": "template",
                "network": False,
                "description": "deterministic local baseline for smoke tests",
            },
            {
                "name": "command",
                "network": "depends_on_command",
                "description": "runs an external command; prompt on stdin, TaskSpec YAML/JSON on stdout",
                "required_args": ["--model-command"],
            },
            {
                "name": "openai",
                "network": True,
                "description": "optional OpenAI Responses API backend via urllib; not used in offline tests",
                "required_args": ["--model or SAT_SIM_OPENAI_MODEL", "--model-api-key-env or OPENAI_API_KEY"],
            },
            {
                "name": "openai_compatible/qwen/vllm",
                "network": "configured_gateway",
                "description": "generic OpenAI-compatible Chat Completions endpoint for local Qwen/vLLM or enterprise gateways; API key optional",
                "required_args": ["--model", "--model-base-url"],
                "structured_output_modes": ["text", "json_object"],
            },
            {
                "name": "deepseek",
                "network": True,
                "description": "optional DeepSeek OpenAI-compatible Chat Completions backend via urllib",
                "required_args": ["DEEPSEEK_API_KEY", "--model or SAT_SIM_DEEPSEEK_MODEL"],
                "structured_output_modes": {
                    "text": "legacy YAML/JSON assistant content",
                    "json_object": "response_format={type: json_object}; assistant content must be valid JSON",
                    "tool_call": "strict generate_task_spec tool call; defaults to https://api.deepseek.com/beta",
                },
                "default_base_url": "https://api.deepseek.com",
                "tool_call_default_base_url": "https://api.deepseek.com/beta",
                "default_model": "deepseek-chat",
            },
        ],
    })
    return 0

def cmd_inventory(args: argparse.Namespace) -> int:
    examples_dir = Path(args.examples_dir)
    specs = []
    if examples_dir.exists():
        for path in sorted(list(examples_dir.glob("*.yaml")) + list(examples_dir.glob("*.yml")) + list(examples_dir.glob("*.json"))):
            try:
                doc = load_task_spec(path)
                if doc.task_id and doc.task_type and doc.schema_version:
                    specs.append({"path": str(path), "task_id": doc.task_id, "task_type": doc.task_type, "schema_version": doc.schema_version})
            except Exception as exc:
                specs.append({"path": str(path), "error": str(exc)})
    _print_json({"ok": True, "examples": specs})
    return 0







def cmd_plan_capability(args: argparse.Namespace) -> int:
    try:
        from .capability_agent import DEFAULT_ALLOWED_CAPABILITIES
        from .agent_policy import decide_agent_interaction
        from .capability_planner import plan_capability_for_request

        text = _read_request_text(args)
        allowed = tuple(args.capability) if getattr(args, "capability", None) else DEFAULT_ALLOWED_CAPABILITIES
        plan = plan_capability_for_request(text, allowed_capabilities=allowed)
        policy = decide_agent_interaction(text, allowed_capabilities=allowed, plan=plan)
        payload = {"ok": plan.ok, "plan": plan.to_dict(), "interaction_policy": policy.to_dict()}
        if args.output:
            write_json(args.output, payload)
        _print_json(payload)
        return 0 if plan.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2

def cmd_capabilities(args: argparse.Namespace) -> int:
    try:
        from .capability_registry import capability_summary_payload

        payload = capability_summary_payload()
        _print_json({"ok": True, "capabilities": payload})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_capability(args: argparse.Namespace) -> int:
    try:
        from .capability_registry import get_capability

        contract = get_capability(args.capability_id)
        _print_json({"ok": True, "capability": contract.to_dict()})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_operator_contracts(args: argparse.Namespace) -> int:
    try:
        from .capability_registry import operator_contract_summary_payload, validate_operator_registry

        contracts = operator_contract_summary_payload(active_only=args.active_only)
        issues = [item.to_dict() for item in validate_operator_registry(active_only=args.active_only)]
        payload = {
            "ok": not any(item["severity"] == "error" for item in issues),
            "operator_contracts": contracts,
            "issues": issues,
        }
        if args.output:
            write_json(args.output, payload)
        _print_json(payload)
        return 0 if payload["ok"] else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_operator_contract(args: argparse.Namespace) -> int:
    try:
        from .capability_registry import get_capability
        from .operator_contract import validate_operator_contract

        operator = get_capability(args.capability_id).operator_contract
        issues = [item.to_dict() for item in validate_operator_contract(operator)]
        _print_json({
            "ok": not any(item["severity"] == "error" for item in issues),
            "operator_contract": operator.model_dump(mode="json"),
            "issues": issues,
        })
        return 0 if not any(item["severity"] == "error" for item in issues) else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_model_route(args: argparse.Namespace) -> int:
    try:
        from .model_router import ModelRouteConfig, route_model

        text = _read_request_text(args) if args.input_kind == "natural_language" else ""
        route = route_model(
            input_kind=args.input_kind,
            request_text=text,
            config=ModelRouteConfig(
                local_backend=args.local_backend,
                remote_backend=args.remote_backend,
                remote_model=args.model,
                remote_base_url=args.model_base_url,
                remote_command=args.model_command,
            ),
        )
        _print_json({"ok": True, "route": route.to_dict()})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2


def cmd_create_task(args: argparse.Namespace) -> int:
    try:
        from .task_spec import load_mapping
        from .unified_agent import UnifiedAgentRequest, run_unified_agent

        kwargs: dict[str, Any] = {}
        if args.input_kind == "natural_language":
            kwargs["request_text"] = _read_request_text(args)
        elif args.input_kind == "form":
            kwargs["form_data"] = load_mapping(args.input)
        elif args.input_kind == "task_spec":
            kwargs["task_spec"] = load_mapping(args.input)
        elif args.input_kind == "patch":
            kwargs["base_task_spec"] = load_mapping(args.base_spec)
            kwargs["patch"] = load_mapping(args.patch)
        result = run_unified_agent(UnifiedAgentRequest(
            input_kind=args.input_kind,
            output_dir=args.output_dir,
            examples_dir=args.examples_dir,
            backend=args.backend,
            local_backend=args.local_backend,
            remote_backend=args.remote_backend,
            model_command=args.model_command,
            model_name=args.model,
            model_base_url=args.model_base_url,
            model_api_key_env=args.model_api_key_env,
            model_timeout_s=args.model_timeout_s,
            model_temperature=args.temperature,
            model_max_output_tokens=args.max_output_tokens,
            model_structured_output=args.structured_output,
            task_id=args.task_id,
            output_root=args.output_root,
            script_output=args.script_output,
            max_repair_attempts=args.max_repairs,
            **kwargs,
        ))
        _print_json(result.to_dict())
        return 0 if result.ok else 1
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2

def cmd_catalog(args: argparse.Namespace) -> int:
    try:
        from .catalog import catalog_payload

        _print_json({"ok": True, "catalog": catalog_payload()})
        return 0
    except Exception as exc:
        _print_json({"ok": False, "error": str(exc)})
        return 2

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sat-sim", description="TaskSpec-driven satellite simulation CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate a TaskSpec YAML/JSON file")
    p.add_argument("spec", type=Path)
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--no-schema", action="store_true", help="skip JSON Schema validation and run semantic checks only")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("migrate", help="migrate a legacy TaskSpec to Canonical TaskSpec 1.0")
    p.add_argument("spec", type=Path)
    p.add_argument("--output", type=Path, required=True, help="write canonical YAML or JSON")
    p.add_argument("--report", type=Path, help="write a migration report JSON")
    p.set_defaults(func=cmd_migrate)

    p = sub.add_parser("resolve", help="resolve a TaskSpec against capability, effect, dependency and output contracts")
    p.add_argument("spec", type=Path)
    p.add_argument("--output", type=Path, help="write ResolvedSpec YAML or JSON")
    p.add_argument("--report", type=Path, help="write plan validation JSON")
    p.set_defaults(func=cmd_resolve)

    p = sub.add_parser("plan", help="build and validate the V24 constrained execution DAG")
    p.add_argument("spec", type=Path)
    p.add_argument("--output-dir", type=Path, default=Path("planned_tasks"))
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("compile", help="compile a TaskSpec into a registered adapter payload after V24 planning")
    p.add_argument("spec", type=Path)
    p.add_argument("--output", type=Path, help="write compiled plan JSON")
    p.add_argument("--no-validate", action="store_true", help="skip validation before compiling")
    p.set_defaults(func=cmd_compile)


    p = sub.add_parser("repair", help="apply V26 finite non-semantic TaskSpec repairs")
    p.add_argument("spec", type=Path)
    p.add_argument("--output", type=Path, help="write repaired canonical TaskSpec")
    p.add_argument("--report", type=Path, help="write structured repair report")
    p.add_argument("--max-repairs", type=int, default=2, choices=[1, 2])
    p.set_defaults(func=cmd_repair)

    p = sub.add_parser("prepare-run", help="prepare a V25 immutable Run Bundle and return the plan hash to commit")
    p.add_argument("spec", type=Path)
    p.add_argument("--output-root", type=Path, default=Path("runs"))
    p.add_argument("--run-id", help="optional explicit unique run ID")
    p.add_argument("--supersedes", help="optional prior run ID superseded by this run")
    p.set_defaults(func=cmd_prepare_run)

    p = sub.add_parser("execute-run", help="commit and execute an exact prepared plan hash")
    p.add_argument("bundle", type=Path)
    p.add_argument("--plan-sha256", required=True, help="exact SHA-256 returned by prepare-run")
    p.add_argument("--max-attempts", type=int, choices=[1, 2], default=2)
    p.add_argument("--hard-timeout", action=argparse.BooleanOptionalAction, default=True, help="run in a killable child process (default: enabled)")
    p.set_defaults(func=cmd_execute_run)

    p = sub.add_parser("cancel-run", help="request cooperative cancellation of an unsealed prepared/running run")
    p.add_argument("bundle", type=Path)
    p.add_argument("--reason", default="user_requested")
    p.set_defaults(func=cmd_cancel_run)

    p = sub.add_parser("verify-run", help="verify sealed Run Bundle artifact hashes")
    p.add_argument("bundle", type=Path)
    p.set_defaults(func=cmd_verify_run)

    p = sub.add_parser("queue-worker", help="claim and execute jobs from the persistent SQLite queue")
    p.add_argument("--database", type=Path, default=Path(".sat_sim_api/execution_queue.sqlite3"))
    p.add_argument("--worker-id")
    p.add_argument("--poll-interval", type=float, default=0.25)
    p.add_argument("--lease-seconds", type=float, default=30.0)
    p.add_argument("--max-jobs", type=int)
    p.add_argument("--idle-timeout", type=float)
    p.set_defaults(func=cmd_queue_worker)

    p = sub.add_parser("remote-worker", help="run an authenticated remote worker over the HTTPS bundle protocol")
    p.add_argument("--control-plane", required=True, help="control-plane base URL, for example https://sim.example.com")
    p.add_argument("--worker-id", required=True)
    p.add_argument("--token-env", default="SAT_SIM_WORKER_TOKEN", help="environment variable containing the worker bearer token")
    p.add_argument("--work-root", type=Path, default=Path(".sat_sim_remote_worker"))
    p.add_argument("--poll-interval", type=float, default=1.0)
    p.add_argument("--heartbeat-interval", type=float, default=5.0)
    p.add_argument("--lease-seconds", type=float, default=30.0)
    p.add_argument("--request-timeout", type=float, default=60.0)
    p.add_argument("--max-jobs", type=int)
    p.add_argument("--idle-timeout", type=float)
    p.add_argument("--label", action="append", help="worker scheduling label; may be repeated")
    p.add_argument("--insecure-skip-tls-verify", action="store_true", help="development only: disable TLS certificate validation")
    p.set_defaults(func=cmd_remote_worker)

    p = sub.add_parser("token-sha256", help="derive a SHA-256 token digest for auth configuration without printing the token")
    p.add_argument("--token-env", help="read token from this environment variable")
    p.add_argument("--token", help="discouraged: token literal; interactive prompt is safer")
    p.set_defaults(func=cmd_token_sha256)

    p = sub.add_parser("queue-status", help="inspect the persistent execution queue")
    p.add_argument("--database", type=Path, default=Path(".sat_sim_api/execution_queue.sqlite3"))
    p.add_argument("--state", action="append", help="filter by queue state; may be repeated")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--offset", type=int, default=0)
    p.set_defaults(func=cmd_queue_status)

    p = sub.add_parser("provider-eval", help="run an attested provider evaluation without allowing fallback scores")
    p.add_argument("--provider-id", required=True)
    p.add_argument("--provider-kind", default="real_llm_provider")
    p.add_argument("--cases", type=Path, default=Path("evals/provider_eval_cases.json"))
    p.add_argument("--output-dir", type=Path, required=True)
    p.add_argument("--readiness-matrix", type=Path)
    p.set_defaults(func=cmd_provider_eval)

    p = sub.add_parser("model-providers", help="show configured local and remote model providers")
    p.add_argument("--config", type=Path)
    p.add_argument("--live-probe", action="store_true")
    p.set_defaults(func=cmd_model_providers)

    p = sub.add_parser("run", help="validate, compile, run, and write a dataset")
    p.add_argument("spec", type=Path)
    p.add_argument("--output", type=Path, help="override outputs.output_root")
    p.add_argument("--no-schema", action="store_true", help="skip JSON Schema validation")
    p.add_argument("--no-dataset", action="store_true", help="run without writing dataset files")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("expand", help="expand a campaign TaskSpec into child TaskSpecs without running them")
    p.add_argument("spec", type=Path)
    p.add_argument("--output", type=Path, help="write campaign plan JSON")
    p.add_argument("--output-root", type=Path, help="override child outputs.output_root values in the expanded plan")
    p.add_argument("--no-schema", action="store_true", help="skip JSON Schema validation")
    p.set_defaults(func=cmd_expand)

    p = sub.add_parser("agent-context", help="print the TaskSpec generation contract for Agents")
    p.add_argument("--examples-dir", default="examples")
    p.set_defaults(func=cmd_agent_context)

    p = sub.add_parser("explain-errors", help="validate and emit Agent-oriented repair hints")
    p.add_argument("spec", type=Path)
    p.add_argument("--no-schema", action="store_true", help="skip JSON Schema validation")
    p.set_defaults(func=cmd_explain_errors)


    p = sub.add_parser("generate", help="generate a TaskSpec from a natural-language request")
    p.add_argument("request", nargs="?", help="natural-language simulation request")
    p.add_argument("--request-file", type=Path, help="read request text from a file")
    p.add_argument("--output-dir", type=Path, default=Path("generated_tasks"), help="directory for generated task artifacts")
    p.add_argument("--examples-dir", default="examples")
    p.add_argument("--backend", default="template", choices=["template"], help="Agent backend; P4 ships deterministic template backend")
    p.add_argument("--task-id", help="override generated task_id")
    p.add_argument("--output-root", type=Path, help="override TaskSpec outputs.output_root")
    p.add_argument("--max-repairs", type=int, default=2)
    p.add_argument("--run", action="store_true", help="execute the generated task after validation/compile")
    p.add_argument("--dry-run", action="store_true", help="skip execution even when --run is set")
    p.add_argument("--json", action="store_true", help="emit full machine-readable JSON report")
    p.set_defaults(func=cmd_generate)



    p = sub.add_parser("agent-prompt", help="build a capability-constrained prompt/context for an external LLM")
    p.add_argument("request", nargs="?", help="natural-language simulation request")
    p.add_argument("--request-file", type=Path, help="read request text from a file")
    p.add_argument("--capability", action="append", help="restrict allowed capability_id; may be repeated")
    p.add_argument("--context-json", action="store_true", help="emit machine-readable capability context instead of a prompt")
    p.add_argument("--compact", action="store_true", default=True, help="use the V36 compact planner-scoped prompt; this is the default")
    p.add_argument("--full-context", action="store_true", help="include the full allowed capability context for debugging")
    p.add_argument("--output", type=Path, help="write prompt/context to this path")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON wrapper")
    p.set_defaults(func=cmd_agent_prompt)

    p = sub.add_parser("generate-script", help="generate a capability-backed simulation script from a request or LLM TaskSpec draft")
    p.add_argument("request", nargs="?", help="natural-language simulation request")
    p.add_argument("--request-file", type=Path, help="read request text from a file")
    p.add_argument("--draft-spec", type=Path, help="TaskSpec YAML/JSON or Markdown-fenced model output from an external LLM")
    p.add_argument("--output-dir", type=Path, default=Path("generated_scripts"), help="artifact directory")
    p.add_argument("--examples-dir", default="examples")
    p.add_argument("--backend", default="template", choices=["template", "command", "openai", "deepseek", "openai_compatible", "qwen", "vllm"], help="draft backend used when --draft-spec is omitted")
    p.add_argument("--model-command", help="for --backend command: external command that reads the prompt on stdin and writes TaskSpec YAML/JSON on stdout")
    p.add_argument("--model", help="model name for openai/deepseek; may also use SAT_SIM_OPENAI_MODEL or SAT_SIM_DEEPSEEK_MODEL")
    p.add_argument("--model-base-url", help="provider endpoint/base URL override; for deepseek may be https://api.deepseek.com or full /chat/completions URL")
    p.add_argument("--model-api-key-env", default="OPENAI_API_KEY", help="environment variable containing API key; deepseek defaults internally to DEEPSEEK_API_KEY when this is left unchanged")
    p.add_argument("--model-timeout-s", type=float, default=60.0, help="external model call timeout in seconds")
    p.add_argument("--temperature", type=float, help="optional model temperature")
    p.add_argument("--max-output-tokens", type=int, help="optional maximum output tokens")
    p.add_argument("--structured-output", choices=["text", "json_object", "tool_call"], default="json_object", help="LLM structured output mode; V36 defaults to JSON object for provider backends")
    p.add_argument("--capability", action="append", help="restrict allowed capability_id; may be repeated")
    p.add_argument("--task-id", help="override generated task_id")
    p.add_argument("--output-root", type=Path, help="override TaskSpec outputs.output_root and generated script dataset path")
    p.add_argument("--script-output", type=Path, help="write generated capability-python script to this path")
    p.add_argument("--max-repairs", type=int, default=2)
    p.add_argument("--run", action="store_true", help="execute the generated TaskSpec after script export")
    p.add_argument("--dry-run", action="store_true", help="skip execution even when --run is set")
    p.add_argument("--json", action="store_true", help="emit full machine-readable JSON report")
    p.set_defaults(func=cmd_generate_script)

    p = sub.add_parser("export-script", help="export a deterministic wrapper script for a TaskSpec")
    p.add_argument("spec", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--kind", choices=["python", "shell", "capability-python"], default="python")
    p.add_argument("--output-root", type=Path, help="override dataset output root in the exported script")
    p.set_defaults(func=cmd_export_script)

    p = sub.add_parser("eval-agent", help="run offline Agent generation eval fixtures")
    p.add_argument("--evals-dir", default="evals")
    p.add_argument("--output-dir", default="reports/agent_eval_p4")
    p.add_argument("--examples-dir", default="examples")
    p.add_argument("--backend", default="template", choices=["template"])
    p.add_argument("--run", action="store_true", help="execute generated tasks during evaluation")
    p.set_defaults(func=cmd_eval_agent)

    p = sub.add_parser("eval-capability-agent", help="evaluate capability-aware TaskSpec/script generation with template, command, or OpenAI backend")
    p.add_argument("--evals-dir", default="evals/capability_agent")
    p.add_argument("--output-dir", default="reports/capability_agent_eval_a2")
    p.add_argument("--examples-dir", default="examples")
    p.add_argument("--backend", default="template", choices=["template", "command", "openai", "deepseek", "openai_compatible", "qwen", "vllm"])
    p.add_argument("--model-command", help="for --backend command: external command that reads prompt on stdin and writes TaskSpec YAML/JSON")
    p.add_argument("--model", help="model name for openai/deepseek; may also use SAT_SIM_OPENAI_MODEL or SAT_SIM_DEEPSEEK_MODEL")
    p.add_argument("--model-base-url", help="provider endpoint/base URL override; for deepseek may be https://api.deepseek.com or full /chat/completions URL")
    p.add_argument("--model-api-key-env", default="OPENAI_API_KEY")
    p.add_argument("--model-timeout-s", type=float, default=60.0)
    p.add_argument("--temperature", type=float)
    p.add_argument("--max-output-tokens", type=int)
    p.add_argument("--structured-output", choices=["text", "json_object", "tool_call"], default="json_object", help="LLM structured output mode; V36 defaults to JSON object for provider backends")
    p.add_argument("--capability", action="append", help="restrict allowed capability_id; may be repeated")
    p.add_argument("--run-scripts", action="store_true", help="execute exported scripts as part of the eval")
    p.add_argument("--script-timeout-s", type=float, default=120.0, help="per-script timeout when --run-scripts is enabled")
    p.set_defaults(func=cmd_eval_capability_agent)

    p = sub.add_parser("eval-source-grounded-agent", help="evaluate source-binding, parameter mapping, and executable script generation")
    p.add_argument("--evals-dir", default="evals/source_grounded_agent")
    p.add_argument("--output-dir", default="reports/source_grounded_eval")
    p.add_argument("--examples-dir", default="examples")
    p.add_argument("--backend", default="template", choices=["template"])
    p.add_argument("--capability", action="append", help="restrict allowed capability_id; may be repeated")
    p.add_argument("--run-scripts", action="store_true", help="execute exported scripts as part of the eval")
    p.add_argument("--script-timeout-s", type=float, default=120.0, help="per-script timeout when --run-scripts is enabled")
    p.set_defaults(func=cmd_eval_source_grounded_agent)

    p = sub.add_parser("llm-backends", help="list supported A2 LLM backend integration modes")
    p.set_defaults(func=cmd_llm_backends)

    p = sub.add_parser("agent-frameworks", help="show recommended mature Agent framework integration boundary")
    p.set_defaults(func=cmd_agent_frameworks)


    p = sub.add_parser("plan-capability", help="plan capability routing for a natural-language request without generating a script")
    p.add_argument("request", nargs="?", help="natural-language simulation request")
    p.add_argument("--request-file", help="read request text from a file")
    p.add_argument("--capability", action="append", help="restrict allowed capability_id; may be repeated")
    p.add_argument("--output", type=Path, help="write planner JSON to this path")
    p.set_defaults(func=cmd_plan_capability)

    p = sub.add_parser("capabilities", help="list machine-readable capability contracts")
    p.set_defaults(func=cmd_capabilities)

    p = sub.add_parser("capability", help="show one machine-readable capability contract")
    p.add_argument("capability_id")
    p.set_defaults(func=cmd_capability)

    p = sub.add_parser("operator-contracts", help="list normalized V22 executable operator contracts")
    p.add_argument("--active-only", action="store_true", help="only include active Agent-exposed capabilities")
    p.add_argument("--output", type=Path, help="write registry payload to JSON")
    p.set_defaults(func=cmd_operator_contracts)

    p = sub.add_parser("operator-contract", help="show one normalized operator contract")
    p.add_argument("capability_id")
    p.set_defaults(func=cmd_operator_contract)

    p = sub.add_parser("model-route", help="preview V23 L0/L1/L2 model routing")
    p.add_argument("request", nargs="?", help="natural-language request")
    p.add_argument("--request-file", type=Path)
    p.add_argument("--input-kind", choices=["natural_language", "form", "task_spec", "patch"], default="natural_language")
    p.add_argument("--local-backend", default="template")
    p.add_argument("--remote-backend", choices=["command", "openai", "deepseek", "openai_compatible", "qwen", "vllm"])
    p.add_argument("--model-command")
    p.add_argument("--model")
    p.add_argument("--model-base-url")
    p.set_defaults(func=cmd_model_route)

    p = sub.add_parser("create-task", help="unified natural-language/form/TaskSpec/patch input to Canonical TaskSpec")
    p.add_argument("request", nargs="?", help="natural-language request when --input-kind natural_language")
    p.add_argument("--request-file", type=Path)
    p.add_argument("--input-kind", choices=["natural_language", "form", "task_spec", "patch"], required=True)
    p.add_argument("--input", type=Path, help="form or TaskSpec YAML/JSON input")
    p.add_argument("--base-spec", type=Path, help="base TaskSpec for patch input")
    p.add_argument("--patch", type=Path, help="JSON/YAML merge patch")
    p.add_argument("--output-dir", type=Path, default=Path("generated_tasks"))
    p.add_argument("--examples-dir", default="examples")
    p.add_argument("--backend", default="auto", choices=["auto", "deterministic", "template", "command", "openai", "deepseek", "openai_compatible", "qwen", "vllm"])
    p.add_argument("--local-backend", default="template")
    p.add_argument("--remote-backend", choices=["command", "openai", "deepseek", "openai_compatible", "qwen", "vllm"])
    p.add_argument("--model-command")
    p.add_argument("--model")
    p.add_argument("--model-base-url")
    p.add_argument("--model-api-key-env", default="OPENAI_API_KEY")
    p.add_argument("--model-timeout-s", type=float, default=60.0)
    p.add_argument("--temperature", type=float)
    p.add_argument("--max-output-tokens", type=int)
    p.add_argument("--structured-output", choices=["text", "json_object", "tool_call"], default="json_object")
    p.add_argument("--task-id")
    p.add_argument("--output-root", type=Path)
    p.add_argument("--script-output", type=Path)
    p.add_argument("--max-repairs", type=int, default=2)
    p.set_defaults(func=cmd_create_task)

    p = sub.add_parser("catalog", help="list import-safe component/subsystem runner targets")
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("inventory", help="list example TaskSpec files")
    p.add_argument("--examples-dir", default="examples")
    p.set_defaults(func=cmd_inventory)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except TaskSpecError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
