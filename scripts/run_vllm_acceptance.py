#!/usr/bin/env python3
"""
用途：对 OpenAI-compatible vLLM 服务执行三项真实模型 TaskSpec 验收。
参数：服务地址、模型、认证环境变量、输出目录和案例数量。
输出：生成逐案例调用证据与 report.json，并以退出码表示门禁结果。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from sat_sim.lmstudio_acceptance import DEFAULT_CASES, run_lmstudio_acceptance
from sat_sim.release_closure import RELEASE_VERSION


def _discover_model(base_url: str, api_key_env: str) -> str:
    headers = {"Accept": "application/json"}
    api_key = os.environ.get(api_key_env)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers=headers,
    )
    with urllib.request.urlopen(request, timeout=15.0) as response:  # nosec B310 - operator-selected endpoint
        payload = json.loads(response.read(2_097_152).decode("utf-8"))
    models = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(models, list) or not models:
        raise RuntimeError("vLLM /v1/models returned no model")
    model = models[0].get("id") if isinstance(models[0], dict) else None
    if not isinstance(model, str) or not model.strip():
        raise RuntimeError("vLLM /v1/models returned an invalid model id")
    return model.strip()


def _is_loopback(base_url: str) -> bool:
    return (urlparse(base_url).hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run representative real-vLLM Agent acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", default="SAT_SIM_LOCAL_VLLM_API_KEY")
    parser.add_argument("--output-dir", type=Path, default=Path("reports/vllm_acceptance"))
    parser.add_argument("--timeout-s", type=float, default=300.0)
    parser.add_argument("--max-output-tokens", type=int, default=8192)
    parser.add_argument("--require-loopback", action="store_true")
    args = parser.parse_args()

    if args.require_loopback and not _is_loopback(args.base_url):
        raise SystemExit("vLLM acceptance requires a loopback endpoint")

    model = args.model or _discover_model(args.base_url, args.api_key_env)
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    semantic = run_lmstudio_acceptance(
        base_url=args.base_url,
        model=model,
        api_key_env=args.api_key_env,
        timeout_s=args.timeout_s,
        output_dir=output_dir / "evidence",
        execution_kind="protocol_fixture",
        cases=DEFAULT_CASES,
        require_loopback=args.require_loopback,
        max_output_tokens=args.max_output_tokens,
        execute_generated=True,
        execution_timeout_s=args.timeout_s,
    )
    semantic_payload = semantic.to_dict()
    case_count = len(DEFAULT_CASES)
    passed = (
        semantic.openai_models.get("ok") is True
        and semantic.structured_probe.get("ok") is True
        and semantic.summary.get("taskspec_passed") == case_count
        and semantic.invocation_evidence_summary.get("verified_count") == case_count
        and semantic.fallback_detected is False
    )
    report = {
        "schema_version": "sat-sim.vllm-acceptance.v1",
        "release_version": RELEASE_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "execution_kind": "actual_vllm",
        "status": "PASS" if passed else "FAIL",
        "ok": passed,
        "base_url": args.base_url,
        "model": model,
        "server_identity": {
            "openai_models": semantic.openai_models,
            "native_lmstudio_identity_required": False,
            "provider_kind": "vllm_openai_compatible",
        },
        "structured_probe": semantic.structured_probe,
        "taskspec_cases": semantic.taskspec_cases,
        "summary": {
            "taskspec_case_count": case_count,
            "taskspec_passed": semantic.summary.get("taskspec_passed"),
            "actual_model_invocation_verified_count": semantic.summary.get("actual_model_invocation_verified_count"),
            "fallback_detected": semantic.fallback_detected,
            "generated_script_executed_count": sum(
                1 for item in semantic.taskspec_cases
                if (item.get("checks") or {}).get("generated_script_executed") is True
            ),
            "run_bundle_validation_passed_count": sum(
                1 for item in semantic.taskspec_cases
                if (item.get("checks") or {}).get("physical_validation_passed") is True
            ),
        },
        "invocation_evidence_summary": semantic.invocation_evidence_summary,
        "semantic_harness": {
            "source_schema_version": semantic_payload.get("schema_version"),
            "note": "The shared OpenAI-compatible semantic harness is reused; LM Studio native identity is not part of the vLLM gate.",
        },
    }
    report_path = output_dir / "vllm_acceptance_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": report["status"],
        "model": model,
        **report["summary"],
        "report": str(report_path),
    }, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
