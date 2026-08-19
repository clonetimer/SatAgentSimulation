"""Product-facing V27 ``sat-agent`` command-line workflow."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from .execution_planner import plan_task_spec
from .golden_eval import default_golden_manifest, run_golden_evals
from .run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from .script_exporter import export_runner_script
from .task_models import CanonicalTaskSpec, canonicalize_task_spec
from .task_spec import load_mapping, load_task_spec
from .task_validator import validate_task_spec
from .agent_guards import evaluate_agent_guards
from .calibration import (
    build_calibration_readiness_report,
    calibration_dataset_schema,
    calibration_readiness_schema,
    calibration_registry_schema,
    load_calibration_manifest,
    register_calibration_manifest,
    validate_calibration_manifest,
)
from .unified_agent import UnifiedAgentRequest, run_unified_agent
from .release_closure import (
    RELEASE_ID,
    RELEASE_VERSION,
    evaluate_release_closure,
    release_manifest,
    run_environment_doctor,
)


def _print(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _read_text(value: str | None, request_file: Path | None) -> str:
    if request_file is not None:
        return request_file.read_text(encoding="utf-8").strip()
    if value:
        return value.strip()
    raise ValueError("request text is required")


def cmd_parse(args: argparse.Namespace) -> int:
    try:
        result = run_unified_agent(UnifiedAgentRequest(
            input_kind="natural_language",
            request_text=_read_text(args.request, args.request_file),
            output_dir=args.output_dir,
            backend=args.backend,
            local_backend=args.local_backend,
            remote_backend=args.remote_backend,
            model_name=args.model,
            model_base_url=args.model_base_url,
            experience_reuse_enabled=args.experience_reuse,
            experience_store_root=args.experience_store,
            experience_tenant_id=args.experience_tenant,
            experience_project_id=args.experience_project,
        ))
        _print(result.to_dict())
        return 0 if result.ok else 1
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_validate(args: argparse.Namespace) -> int:
    try:
        canonical = canonicalize_task_spec(load_task_spec(args.spec).data)
        validation = validate_task_spec(canonical)
        guards = evaluate_agent_guards(canonical)
        payload = {"ok": validation.ok and guards.ok, "validation": validation.to_dict(), "guards": guards.to_dict()}
        _print(payload)
        return 0 if payload["ok"] else 1
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_resolve(args: argparse.Namespace) -> int:
    try:
        planning = plan_task_spec(load_task_spec(args.spec).data)
        payload = planning.to_dict()
        _print({"ok": planning.ok, "planning": payload})
        return 0 if planning.ok else 1
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_plan(args: argparse.Namespace) -> int:
    return cmd_resolve(args)


def _finish_isolated_run(payload: Mapping[str, Any], return_code: int, *, enabled: bool) -> int:
    """Flush sealed-run status and bypass unreliable native-object teardown.

    This mode is intentionally private to subprocess-based verification suites.
    A few Basilisk/SWIG fault graphs can finish and seal all evidence but spend
    unbounded time in interpreter finalization.  ``os._exit`` is safe here only
    because the Run Bundle has already been finalized and the minimal status has
    been synchronously flushed.  Normal interactive CLI invocations never use it.
    """
    _print(dict(payload))
    if enabled:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(int(return_code))
    return int(return_code)


def cmd_run(args: argparse.Namespace) -> int:
    try:
        spec = load_task_spec(args.spec).data
        prepared = prepare_run(spec, output_root=args.output_root, run_id=args.run_id, supersedes_run_id=args.supersedes)
        execution = execute_prepared_run(
            prepared.bundle_root,
            expected_plan_sha256=prepared.execution_plan_sha256,
            max_attempts=args.max_attempts,
            hard_timeout=args.hard_timeout,
        )
        ok = execution.run_record.status.value == "SUCCEEDED" and execution.validation.result.value == "PASS"
        return_code = 0 if ok else 1
        if bool(getattr(args, "isolated_process_exit", False)):
            return _finish_isolated_run(
                {
                    "ok": ok,
                    "bundle_root": str(prepared.bundle_root),
                    "run_record_status": execution.run_record.status.value,
                    "validation_result": execution.validation.result.value,
                    "sealed": bool(execution.run_record.sealed),
                    "isolated_process_exit": True,
                },
                return_code,
                enabled=True,
            )
        _print({"ok": ok, "prepared_run": prepared.model_dump(mode="json"), "execution": execution.model_dump(mode="json")})
        return return_code
    except Exception as exc:
        return _finish_isolated_run(
            {"ok": False, "error": str(exc), "isolated_process_exit": bool(getattr(args, "isolated_process_exit", False))},
            2,
            enabled=bool(getattr(args, "isolated_process_exit", False)),
        )


def cmd_export(args: argparse.Namespace) -> int:
    try:
        result = export_runner_script(args.spec, args.output, kind=args.kind, output_root=args.output_root)
        _print({"ok": True, "script": result.to_dict()})
        return 0
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def _run_root(bundle: Path) -> dict[str, Any]:
    def read(relative: str) -> dict[str, Any] | None:
        path = bundle / relative
        if not path.exists():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    return {
        "bundle": str(bundle),
        "run_record": read("run_record.json"),
        "prepared_run": read("runtime/prepared_run.json"),
        "validation": read("validation/validation_outcome.json"),
        "claim_report": read("validation/claim_report.json"),
        "summary": read("results/summary.json"),
        "integrity": verify_run_bundle(bundle) if (bundle / "bundle_manifest.json").exists() else None,
    }


def cmd_inspect(args: argparse.Namespace) -> int:
    try:
        payload = _run_root(args.bundle)
        _print({"ok": True, "run": payload})
        return 0
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_report(args: argparse.Namespace) -> int:
    try:
        payload = _run_root(args.bundle)
        record = payload.get("run_record") or {}
        report = {
            "run_id": record.get("run_id"),
            "status": record.get("status"),
            "validation": payload.get("validation"),
            "claim_report": payload.get("claim_report"),
            "summary": payload.get("summary"),
            "integrity": payload.get("integrity"),
        }
        _print({"ok": True, "report": report})
        return 0
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_eval(args: argparse.Namespace) -> int:
    try:
        report = run_golden_evals(
            args.manifest or default_golden_manifest(),
            output_dir=args.output_dir,
            repeat_count=args.repeat_count,
            execute_marked_cases=args.execute_marked,
            max_cases=args.max_cases,
        )
        _print({"ok": report.ok, "report": report.to_dict()})
        return 0 if report.ok else 1
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_schema(args: argparse.Namespace) -> int:
    _print({"ok": True, "schema": CanonicalTaskSpec.model_json_schema()})
    return 0


def cmd_openapi(args: argparse.Namespace) -> int:
    try:
        from .api import create_app
        app = create_app(runs_root=args.runs_root, artifacts_root=args.artifacts_root)
        payload = app.openapi()
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _print({"ok": True, "openapi": payload, "output": str(args.output) if args.output else None})
        return 0
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
        from .api import create_app
        app = create_app(runs_root=args.runs_root, artifacts_root=args.artifacts_root)
        uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
        return 0
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def _write_optional_json(payload: Any, output: Path | None) -> None:
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def cmd_calibration_check(args: argparse.Namespace) -> int:
    try:
        report = build_calibration_readiness_report(project_root=args.project_root, registry_path=args.registry)
        payload = {"ok": report.status == "PASS", "calibration": report.model_dump(mode="json")}
        _write_optional_json(payload, args.output)
        _print(payload)
        return 0 if payload["ok"] else 1
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_calibration_validate(args: argparse.Namespace) -> int:
    try:
        manifest = load_calibration_manifest(args.manifest)
        report = validate_calibration_manifest(manifest, manifest_path=args.manifest, project_root=args.project_root)
        payload = {"ok": report.status == "PASS", "validation": report.model_dump(mode="json")}
        _write_optional_json(payload, args.output)
        _print(payload)
        return 0 if payload["ok"] else 1
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_calibration_report(args: argparse.Namespace) -> int:
    return cmd_calibration_check(args)


def cmd_calibration_register(args: argparse.Namespace) -> int:
    try:
        payload = register_calibration_manifest(
            args.manifest,
            project_root=args.project_root,
            registry_path=args.registry,
            commit=args.commit,
        )
        _write_optional_json(payload, args.output)
        _print({"ok": payload.get("status") == "PASS", **payload})
        return 0 if payload.get("status") == "PASS" else 1
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_calibration_schema(args: argparse.Namespace) -> int:
    schemas = {
        "dataset": calibration_dataset_schema(),
        "registry": calibration_registry_schema(),
        "readiness": calibration_readiness_schema(),
    }
    selected = schemas if args.kind == "all" else {args.kind: schemas[args.kind]}
    payload = {"ok": True, "schemas": selected}
    _write_optional_json(payload, args.output)
    _print(payload)
    return 0


def cmd_version(args: argparse.Namespace) -> int:
    _print({"ok": True, "release_id": RELEASE_ID, "version": RELEASE_VERSION, "manifest": release_manifest()})
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    try:
        report = run_environment_doctor(
            runs_root=args.runs_root,
            artifacts_root=args.artifacts_root,
            strict_assets=args.strict_assets,
            require_api=args.require_api,
            smoke=not args.no_smoke,
        )
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        _print({"ok": report.ok, "doctor": report.model_dump(mode="json"), "output": str(args.output) if args.output else None})
        return 0 if report.ok else 1
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def cmd_release_check(args: argparse.Namespace) -> int:
    try:
        report = evaluate_release_closure(
            golden_report=args.golden_report,
            output_root=args.output_dir,
            source_root=args.source_root,
            strict_assets=args.strict_assets,
            execute_representative=not args.no_representative,
        )
        _print({"ok": report.ok, "release": report.model_dump(mode="json"), "output_dir": str(args.output_dir)})
        return 0 if report.ok else 1
    except Exception as exc:
        _print({"ok": False, "error": str(exc)})
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sat-agent", description="Constrained satellite simulation Agent workflow")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("parse", help="parse natural language to a Canonical TaskSpec")
    p.add_argument("request", nargs="?")
    p.add_argument("--request-file", type=Path)
    p.add_argument("--output-dir", type=Path, default=Path("generated_tasks"))
    p.add_argument("--backend", default="auto", choices=["auto", "template", "command", "openai", "deepseek", "openai_compatible", "qwen", "vllm"])
    p.add_argument("--local-backend", default="template")
    p.add_argument("--remote-backend", choices=["command", "openai", "deepseek", "openai_compatible", "qwen", "vllm"])
    p.add_argument("--model")
    p.add_argument("--model-base-url")
    p.add_argument("--experience-reuse", action="store_true")
    p.add_argument("--experience-store", type=Path)
    p.add_argument("--experience-tenant")
    p.add_argument("--experience-project")
    p.set_defaults(func=cmd_parse)

    for name, help_text, func in (
        ("validate", "validate TaskSpec schema and guards", cmd_validate),
        ("resolve", "resolve capability/effect/output ownership", cmd_resolve),
        ("plan", "build the validated execution DAG", cmd_plan),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("spec", type=Path)
        p.set_defaults(func=func)

    p = sub.add_parser("run", help="prepare, commit and execute an immutable Run Bundle")
    p.add_argument("spec", type=Path)
    p.add_argument("--output-root", type=Path, default=Path("runs"))
    p.add_argument("--run-id")
    p.add_argument("--supersedes")
    p.add_argument("--max-attempts", type=int, choices=[1, 2], default=1)
    p.add_argument("--hard-timeout", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--isolated-process-exit", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("export-script", help="export a deterministic reproduction script")
    p.add_argument("spec", type=Path)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--kind", choices=["python", "shell", "capability-python"], default="capability-python")
    p.add_argument("--output-root", type=Path)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("inspect", help="inspect a Run Bundle")
    p.add_argument("bundle", type=Path)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("report", help="show ValidationOutcome and ClaimReport")
    p.add_argument("bundle", type=Path)
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("eval", help="run the V27 formal Agent Golden Set")
    p.add_argument("--manifest", type=Path, help="Golden Set manifest; defaults to the manifest bundled in the installed package")
    p.add_argument("--output-dir", type=Path, default=Path("reports/agent_golden_v27"))
    p.add_argument("--repeat-count", type=int, choices=[1, 3, 5], default=3)
    p.add_argument("--execute-marked", action="store_true")
    p.add_argument("--max-cases", type=int)
    p.set_defaults(func=cmd_eval)

    p = sub.add_parser("schema", help="print the Canonical TaskSpec JSON Schema")
    p.set_defaults(func=cmd_schema)

    p = sub.add_parser("openapi", help="render the local API OpenAPI document")
    p.add_argument("--runs-root", type=Path, default=Path("runs"))
    p.add_argument("--artifacts-root", type=Path, default=Path(".sat_sim_api"))
    p.add_argument("--output", type=Path)
    p.set_defaults(func=cmd_openapi)

    p = sub.add_parser("serve", help="serve the local FastAPI application")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--runs-root", type=Path, default=Path("runs"))
    p.add_argument("--artifacts-root", type=Path, default=Path(".sat_sim_api"))
    p.add_argument("--log-level", default="info")
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("calibration", help="validate calibration evidence and dataset readiness")
    calibration_sub = p.add_subparsers(dest="calibration_command", required=True)

    for calibration_name, calibration_help, calibration_func in (
        ("check", "check the dataset registry, parameter bindings and current Claim ceiling", cmd_calibration_check),
        ("report", "emit the current calibration readiness report", cmd_calibration_report),
    ):
        cp = calibration_sub.add_parser(calibration_name, help=calibration_help)
        cp.add_argument("--project-root", type=Path, default=Path("."))
        cp.add_argument("--registry", type=Path)
        cp.add_argument("--output", type=Path)
        cp.set_defaults(func=calibration_func)

    cp = calibration_sub.add_parser("validate", help="validate one calibration dataset manifest and referenced files")
    cp.add_argument("manifest", type=Path)
    cp.add_argument("--project-root", type=Path, default=Path("."))
    cp.add_argument("--output", type=Path)
    cp.set_defaults(func=cmd_calibration_validate)

    cp = calibration_sub.add_parser("register", help="validate and optionally register a qualified dataset manifest")
    cp.add_argument("manifest", type=Path)
    cp.add_argument("--project-root", type=Path, default=Path("."))
    cp.add_argument("--registry", type=Path)
    cp.add_argument("--commit", action="store_true", help="write the registry; without this flag the command is a dry run")
    cp.add_argument("--output", type=Path)
    cp.set_defaults(func=cmd_calibration_register)

    cp = calibration_sub.add_parser("schema", help="print calibration dataset/registry/readiness JSON Schemas")
    cp.add_argument("--kind", choices=["dataset", "registry", "readiness", "all"], default="all")
    cp.add_argument("--output", type=Path)
    cp.set_defaults(func=cmd_calibration_schema)

    p = sub.add_parser("version", help="show the current release identity and manifest")
    p.set_defaults(func=cmd_version)

    p = sub.add_parser("doctor", help="check runtime dependencies, registries, assets and a core planning smoke")
    p.add_argument("--runs-root", type=Path, default=Path("runs"))
    p.add_argument("--artifacts-root", type=Path, default=Path(".sat_sim_api"))
    p.add_argument("--strict-assets", action="store_true", help="treat missing WMM/SPICE assets as a failure")
    p.add_argument("--require-api", action="store_true", help="require FastAPI/Uvicorn/HTTPX")
    p.add_argument("--no-smoke", action="store_true")
    p.add_argument("--output", type=Path)
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("release-check", help="evaluate the twelve release termination conditions for the V33 refrozen package")
    p.add_argument("--golden-report", type=Path)
    p.add_argument("--output-dir", type=Path, default=Path("reports/agent_release_v28"))
    p.add_argument("--source-root", type=Path)
    p.add_argument("--strict-assets", action="store_true")
    p.add_argument("--no-representative", action="store_true")
    p.set_defaults(func=cmd_release_check)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
