"""Auditable local/remote model provider catalog and routing policy.

Provider profiles describe how an untrusted draft model can be reached.  They
never grant authority over capability truth, effect ownership, validation or
execution.  Secrets are referenced only by environment-variable name.
"""
from __future__ import annotations

import json
import os
import shlex
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, Mapping

from .model_router import ModelRouteConfig, ModelRouteDecision, route_model

MODEL_PROVIDER_REGISTRY_VERSION = "sat-sim.model-provider-registry.v2"


def _normalized_base_url(base_url: str | None) -> str:
    return str(base_url or "").rstrip("/")


def _openai_models_url(base_url: str | None) -> str:
    base = _normalized_base_url(base_url)
    if base.endswith("/v1"):
        return base + "/models"
    return base + "/v1/models"


def _openai_chat_url(base_url: str | None) -> str:
    base = _normalized_base_url(base_url)
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _ollama_root(base_url: str | None) -> str:
    base = _normalized_base_url(base_url)
    return base[:-3] if base.endswith("/v1") else base


def _http_json(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    body: Mapping[str, Any] | None = None,
    timeout_s: float = 5.0,
    max_bytes: int = 1048576,
) -> tuple[int, Any, float]:
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    data = None
    if body is not None:
        request_headers.setdefault("Content-Type", "application/json")
        data = json.dumps(dict(body), ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    started = time.perf_counter()
    with urllib.request.urlopen(request, timeout=float(timeout_s)) as response:  # nosec B310 - operator-configured model endpoint
        status = int(response.status)
        raw = response.read(max_bytes).decode("utf-8", errors="replace")
    latency_ms = round((time.perf_counter() - started) * 1000.0, 2)
    return status, json.loads(raw) if raw else {}, latency_ms


def _diagnostic_error(exc: Exception) -> tuple[str, str, dict[str, Any]]:
    if isinstance(exc, urllib.error.HTTPError):
        try:
            raw = exc.read(65536).decode("utf-8", errors="replace")
        except Exception:
            raw = ""
        code = int(exc.code)
        status = {401: "AUTHENTICATION_FAILED", 403: "AUTHORIZATION_FAILED", 404: "ENDPOINT_OR_MODEL_NOT_FOUND", 429: "RATE_LIMITED"}.get(code, "HTTP_ERROR")
        return status, f"HTTP {code}", {"http_status": code, "response_excerpt": raw[:1000]}
    if isinstance(exc, urllib.error.URLError):
        reason = str(getattr(exc, "reason", exc))
        lowered = reason.lower()
        status = "CONNECTION_REFUSED" if "refused" in lowered else "UNREACHABLE"
        return status, reason, {}
    if isinstance(exc, TimeoutError):
        return "TIMEOUT", str(exc), {}
    return "PROBE_FAILED", str(exc), {}
ProviderLocation = Literal["deterministic", "local", "remote"]
RoutingMode = Literal["auto", "local", "remote"]


@dataclass(frozen=True)
class ModelProviderProfile:
    provider_id: str
    label: str
    backend: str
    location: ProviderLocation
    tier: Literal["L0", "L1", "L2"]
    model: str | None = None
    base_url: str | None = None
    api_key_env: str | None = None
    command: str | None = None
    structured_output: str = "json_object"
    timeout_s: float = 60.0
    temperature: float | None = 0.0
    max_output_tokens: int | None = 4000
    priority: int = 100
    enabled: bool = True
    source: str = "builtin"
    description: str = ""
    capabilities: tuple[str, ...] = field(default_factory=lambda: ("taskspec_generation",))

    def to_dict(self, *, include_runtime: bool = False) -> dict[str, Any]:
        payload = asdict(self)
        payload["capabilities"] = list(self.capabilities)
        payload["api_key_configured"] = bool(self.api_key_env and os.environ.get(self.api_key_env))
        payload["configured"] = self.configured
        payload["secret_values_exposed"] = False
        if not include_runtime:
            payload.pop("command", None)
        return payload

    @property
    def configured(self) -> bool:
        if not self.enabled:
            return False
        if self.backend in {"template", "deterministic"}:
            return True
        if self.backend == "command":
            return bool(self.command)
        if self.backend in {"openai_compatible", "qwen", "vllm"}:
            return bool(self.model and self.base_url)
        if self.backend in {"deepseek", "openai"}:
            return bool(self.model and self.api_key_env and os.environ.get(self.api_key_env))
        return False


@dataclass(frozen=True)
class ProviderReadiness:
    provider_id: str
    configured: bool
    ready: bool
    live_probe: bool
    status: str
    reason: str
    endpoint: str | None = None
    model: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProviderSelection:
    provider: ModelProviderProfile
    route: ModelRouteDecision
    routing_mode: RoutingMode
    requested_provider_id: str | None
    fallback_used: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "registry_version": MODEL_PROVIDER_REGISTRY_VERSION,
            "routing_mode": self.routing_mode,
            "requested_provider_id": self.requested_provider_id,
            "selected_provider": self.provider.to_dict(),
            "route": self.route.to_dict(),
            "fallback_used": self.fallback_used,
            "reason": self.reason,
            "authority_boundary": "Capability Registry and deterministic guards remain authoritative.",
        }


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def builtin_provider_profiles() -> list[ModelProviderProfile]:
    return [
        ModelProviderProfile(
            provider_id="local-template",
            label="本地确定性模板",
            backend="template",
            location="local",
            tier="L1",
            priority=10,
            description="离线、可重复的 TaskSpec 基线和回退路径。",
        ),
        ModelProviderProfile(
            provider_id="local-command",
            label="本地命令模型",
            backend="command",
            location="local",
            tier="L1",
            command=_env("SAT_SIM_LOCAL_MODEL_COMMAND"),
            priority=20,
            description="通过 stdin/stdout 调用本地模型 CLI 或企业 Agent 包装器。",
        ),
        ModelProviderProfile(
            provider_id="local-ollama",
            label="本地 Ollama",
            backend="openai_compatible",
            location="local",
            tier="L2",
            model=_env("SAT_SIM_OLLAMA_MODEL"),
            base_url=_env("SAT_SIM_OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
            api_key_env=_env("SAT_SIM_OLLAMA_API_KEY_ENV", "SAT_SIM_OLLAMA_API_KEY"),
            priority=25,
            description="本机或局域网 Ollama OpenAI-compatible 服务；通常无需 API Key。",
        ),
        ModelProviderProfile(
            provider_id="local-lmstudio",
            label="本地 LM Studio",
            backend="openai_compatible",
            location="local",
            tier="L2",
            model=_env("SAT_SIM_LMSTUDIO_MODEL"),
            base_url=_env("SAT_SIM_LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1"),
            api_key_env=_env("SAT_SIM_LMSTUDIO_API_KEY_ENV", "SAT_SIM_LMSTUDIO_API_KEY"),
            structured_output="json_schema",
            timeout_s=300.0,
            max_output_tokens=2048,
            priority=27,
            description="本机 LM Studio OpenAI-compatible 服务；通常无需 API Key。",
        ),
        ModelProviderProfile(
            provider_id="local-vllm",
            label="本地 vLLM / Qwen",
            backend="vllm",
            location="local",
            tier="L2",
            model=_env("SAT_SIM_LOCAL_VLLM_MODEL") or _env("SAT_SIM_OPENAI_COMPATIBLE_MODEL"),
            base_url=_env("SAT_SIM_LOCAL_VLLM_BASE_URL", "http://127.0.0.1:8001/v1") or _env("SAT_SIM_OPENAI_COMPATIBLE_BASE_URL"),
            api_key_env=_env("SAT_SIM_LOCAL_VLLM_API_KEY_ENV", "SAT_SIM_LOCAL_VLLM_API_KEY"),
            priority=30,
            description="本机或局域网 OpenAI-compatible vLLM/Qwen 服务。",
        ),
        ModelProviderProfile(
            provider_id="remote-deepseek",
            label="远程 DeepSeek",
            backend="deepseek",
            location="remote",
            tier="L2",
            model=_env("SAT_SIM_DEEPSEEK_MODEL", "deepseek-chat"),
            base_url=_env("SAT_SIM_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            api_key_env="DEEPSEEK_API_KEY",
            priority=40,
            description="DeepSeek Chat Completions；仅在配置密钥时可用。",
        ),
        ModelProviderProfile(
            provider_id="remote-openai",
            label="远程 OpenAI",
            backend="openai",
            location="remote",
            tier="L2",
            model=_env("SAT_SIM_OPENAI_MODEL"),
            base_url=_env("SAT_SIM_OPENAI_BASE_URL", "https://api.openai.com/v1/responses"),
            api_key_env="OPENAI_API_KEY",
            priority=50,
            description="OpenAI Responses API；仅在配置模型和密钥时可用。",
        ),
        ModelProviderProfile(
            provider_id="remote-openai-compatible",
            label="远程 OpenAI 兼容网关",
            backend="openai_compatible",
            location="remote",
            tier="L2",
            model=_env("SAT_SIM_REMOTE_MODEL"),
            base_url=_env("SAT_SIM_REMOTE_MODEL_BASE_URL"),
            api_key_env=_env("SAT_SIM_REMOTE_MODEL_API_KEY_ENV", "SAT_SIM_REMOTE_MODEL_API_KEY"),
            priority=60,
            description="企业网关或远程 vLLM/Qwen OpenAI-compatible 服务。",
        ),
    ]


def _profile_from_mapping(payload: Mapping[str, Any], *, source: str) -> ModelProviderProfile:
    capabilities = payload.get("capabilities") or ("taskspec_generation",)
    return ModelProviderProfile(
        provider_id=str(payload["provider_id"]),
        label=str(payload.get("label") or payload["provider_id"]),
        backend=str(payload.get("backend") or "template"),
        location=str(payload.get("location") or "local"),  # type: ignore[arg-type]
        tier=str(payload.get("tier") or "L1"),  # type: ignore[arg-type]
        model=str(payload["model"]) if payload.get("model") else None,
        base_url=str(payload["base_url"]) if payload.get("base_url") else None,
        api_key_env=str(payload["api_key_env"]) if payload.get("api_key_env") else None,
        command=str(payload["command"]) if payload.get("command") else None,
        structured_output=str(payload.get("structured_output") or ("json_schema" if str(payload.get("provider_id") or "") == "local-lmstudio" else "json_object")),
        timeout_s=(
            300.0
            if str(payload.get("provider_id") or "") == "local-lmstudio" and float(payload.get("timeout_s", 300.0)) <= 60.0
            else float(payload.get("timeout_s", 300.0 if str(payload.get("provider_id") or "") == "local-lmstudio" else 60.0))
        ),
        temperature=float(payload["temperature"]) if payload.get("temperature") is not None else None,
        max_output_tokens=(
            min(int(payload["max_output_tokens"]), 65536)
            if payload.get("max_output_tokens") is not None
            else (2048 if str(payload.get("provider_id") or "") == "local-lmstudio" else None)
        ),
        priority=int(payload.get("priority", 100)),
        enabled=bool(payload.get("enabled", True)),
        source=source,
        description=str(payload.get("description") or ""),
        capabilities=tuple(str(item) for item in capabilities),
    )


class ModelProviderRegistry:
    def __init__(self, profiles: list[ModelProviderProfile] | None = None, *, config_path: str | Path | None = None) -> None:
        merged: dict[str, ModelProviderProfile] = {item.provider_id: item for item in (profiles or builtin_provider_profiles())}
        path = Path(config_path or os.environ.get("SAT_SIM_MODEL_PROVIDERS_FILE", "")).expanduser() if (config_path or os.environ.get("SAT_SIM_MODEL_PROVIDERS_FILE")) else None
        if path:
            data = json.loads(path.read_text(encoding="utf-8"))
            rows = data.get("providers", []) if isinstance(data, Mapping) else data
            if not isinstance(rows, list):
                raise ValueError("model provider config must contain a providers list")
            for payload in rows:
                if isinstance(payload, Mapping):
                    profile = _profile_from_mapping(payload, source=str(path))
                    merged[profile.provider_id] = profile
        self._profiles = merged

    def list(self, *, enabled_only: bool = False) -> list[ModelProviderProfile]:
        rows = [item for item in self._profiles.values() if item.enabled or not enabled_only]
        return sorted(rows, key=lambda item: (item.priority, item.provider_id))

    def get(self, provider_id: str) -> ModelProviderProfile:
        try:
            return self._profiles[provider_id]
        except KeyError as exc:
            raise KeyError(f"unknown model provider: {provider_id}") from exc

    def readiness(self, provider_id: str, *, live_probe: bool = False, timeout_s: float = 3.0) -> ProviderReadiness:
        profile = self.get(provider_id)
        if not profile.enabled:
            return ProviderReadiness(provider_id, False, False, live_probe, "DISABLED", "provider is disabled", profile.base_url, profile.model)
        if profile.backend in {"template", "deterministic"}:
            return ProviderReadiness(provider_id, True, True, live_probe, "READY", "deterministic provider is always available", model=profile.model)
        if profile.backend == "command":
            if not profile.command:
                return ProviderReadiness(provider_id, False, False, live_probe, "NOT_CONFIGURED", "SAT_SIM_LOCAL_MODEL_COMMAND is not configured")
            argv = shlex.split(profile.command)
            executable = shutil.which(argv[0]) if argv else None
            ready = bool(argv and (executable or Path(argv[0]).exists()))
            return ProviderReadiness(
                provider_id,
                True,
                ready,
                live_probe,
                "READY" if ready else "UNAVAILABLE",
                "command executable resolved" if ready else "command executable not found",
                model=profile.model,
                details={"executable": executable or (argv[0] if argv else None)},
            )
        if not profile.configured:
            missing: list[str] = []
            if not profile.model:
                missing.append("model")
            if profile.backend in {"openai_compatible", "qwen", "vllm"} and not profile.base_url:
                missing.append("base_url")
            if profile.backend in {"deepseek", "openai"} and not (profile.api_key_env and os.environ.get(profile.api_key_env)):
                missing.append(profile.api_key_env or "api_key_env")
            return ProviderReadiness(provider_id, False, False, live_probe, "NOT_CONFIGURED", "missing: " + ", ".join(missing), profile.base_url, profile.model)
        if not live_probe:
            return ProviderReadiness(provider_id, True, True, False, "CONFIGURED", "configuration is complete; live endpoint was not called", profile.base_url, profile.model)
        if profile.backend in {"deepseek", "openai"}:
            # Avoid chargeable inference during a readiness probe. Configuration
            # completeness is the safe readiness boundary for hosted vendors.
            return ProviderReadiness(provider_id, True, True, False, "CONFIGURED", "hosted provider configuration complete; no chargeable request sent", profile.base_url, profile.model)
        endpoint = (profile.base_url or "").rstrip("/")
        if endpoint.endswith("/v1"):
            models_url = endpoint + "/models"
        else:
            models_url = endpoint + "/v1/models"
        headers: dict[str, str] = {}
        key = os.environ.get(profile.api_key_env or "") if profile.api_key_env else None
        if key:
            headers["Authorization"] = f"Bearer {key}"
        request = urllib.request.Request(models_url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:  # nosec B310 - configured endpoint
                status = int(response.status)
                raw = response.read(262144).decode("utf-8", errors="replace")
            payload = json.loads(raw) if raw else {}
            model_ids = []
            if isinstance(payload, Mapping) and isinstance(payload.get("data"), list):
                model_ids = [str(item.get("id")) for item in payload["data"] if isinstance(item, Mapping) and item.get("id")]
            model_found = not model_ids or profile.model in model_ids
            return ProviderReadiness(
                provider_id,
                True,
                200 <= status < 300 and model_found,
                True,
                "READY" if model_found else "MODEL_NOT_LISTED",
                "endpoint reachable" if model_found else "endpoint reachable but configured model not listed",
                models_url,
                profile.model,
                {"http_status": status, "model_count": len(model_ids), "model_found": model_found},
            )
        except urllib.error.HTTPError as exc:
            return ProviderReadiness(provider_id, True, False, True, "HTTP_ERROR", f"HTTP {exc.code}", models_url, profile.model)
        except Exception as exc:
            return ProviderReadiness(provider_id, True, False, True, "UNREACHABLE", str(exc), models_url, profile.model)

    def discover_models(self, provider_id: str, *, timeout_s: float = 5.0) -> dict[str, Any]:
        profile = self.get(provider_id)
        if profile.location == "remote" and profile.backend in {"deepseek", "openai"}:
            return {
                "provider_id": provider_id,
                "ok": False,
                "status": "DISCOVERY_NOT_SUPPORTED",
                "reason": "托管模型不通过发现接口探测，请显式配置模型名称。",
                "models": [],
                "secret_values_exposed": False,
            }
        if not profile.base_url:
            return {
                "provider_id": provider_id,
                "ok": False,
                "status": "BASE_URL_NOT_CONFIGURED",
                "reason": "未配置模型服务地址。",
                "models": [],
                "secret_values_exposed": False,
            }
        headers: dict[str, str] = {}
        key = os.environ.get(profile.api_key_env or "") if profile.api_key_env else None
        if key:
            headers["Authorization"] = f"Bearer {key}"
        attempts: list[dict[str, Any]] = []
        urls = [_openai_models_url(profile.base_url)]
        if profile.provider_id == "local-ollama":
            urls.append(_ollama_root(profile.base_url) + "/api/tags")
        for url in dict.fromkeys(urls):
            try:
                status_code, payload, latency_ms = _http_json(url, headers=headers, timeout_s=timeout_s)
                model_ids: list[str] = []
                if isinstance(payload, Mapping) and isinstance(payload.get("data"), list):
                    model_ids = [str(item.get("id")) for item in payload["data"] if isinstance(item, Mapping) and item.get("id")]
                elif isinstance(payload, Mapping) and isinstance(payload.get("models"), list):
                    for item in payload["models"]:
                        if isinstance(item, Mapping):
                            value = item.get("name") or item.get("model")
                            if value:
                                model_ids.append(str(value))
                model_ids = sorted(dict.fromkeys(model_ids))
                attempts.append({"url": url, "http_status": status_code, "latency_ms": latency_ms, "model_count": len(model_ids)})
                if 200 <= status_code < 300:
                    return {
                        "provider_id": provider_id,
                        "ok": True,
                        "status": "READY",
                        "reason": "模型列表读取成功。",
                        "endpoint": url,
                        "latency_ms": latency_ms,
                        "models": model_ids,
                        "configured_model": profile.model,
                        "configured_model_found": bool(profile.model and profile.model in model_ids),
                        "attempts": attempts,
                        "secret_values_exposed": False,
                    }
            except Exception as exc:
                status, reason, details = _diagnostic_error(exc)
                attempts.append({"url": url, "status": status, "reason": reason, **details})
        last = attempts[-1] if attempts else {}
        return {
            "provider_id": provider_id,
            "ok": False,
            "status": str(last.get("status") or "DISCOVERY_FAILED"),
            "reason": str(last.get("reason") or "无法读取模型列表。"),
            "models": [],
            "attempts": attempts,
            "secret_values_exposed": False,
        }

    def probe_generation(self, provider_id: str, *, timeout_s: float = 15.0) -> dict[str, Any]:
        profile = self.get(provider_id)
        if profile.location == "remote":
            return {
                "provider_id": provider_id,
                "ok": False,
                "status": "REMOTE_INFERENCE_PROBE_DISABLED",
                "reason": "诊断接口不会自动发送可能计费的远程推理请求。",
                "secret_values_exposed": False,
            }
        if profile.backend in {"template", "deterministic"}:
            return {
                "provider_id": provider_id,
                "ok": True,
                "status": "READY",
                "reason": "确定性模板无需模型推理。",
                "json_output": True,
                "taskspec_draft_capable": True,
                "secret_values_exposed": False,
            }
        if profile.backend == "command":
            readiness = self.readiness(provider_id, live_probe=True, timeout_s=min(timeout_s, 5.0))
            return {**readiness.to_dict(), "ok": readiness.ready, "taskspec_draft_capable": readiness.ready, "secret_values_exposed": False}
        if not profile.model or not profile.base_url:
            return {
                "provider_id": provider_id,
                "ok": False,
                "status": "NOT_CONFIGURED",
                "reason": "需要配置服务地址和模型名称。",
                "secret_values_exposed": False,
            }

        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        key = os.environ.get(profile.api_key_env or "") if profile.api_key_env else None
        if key:
            headers["Authorization"] = f"Bearer {key}"
        url = _openai_chat_url(profile.base_url)
        attempts: list[dict[str, Any]] = []

        def parse_mapping(text: str) -> tuple[Mapping[str, Any] | None, str]:
            cleaned = text.strip()
            candidates = [cleaned]
            if cleaned.startswith("```"):
                lines = cleaned.splitlines()
                if len(lines) >= 3:
                    candidates.append("\n".join(lines[1:-1]).strip())
            left, right = cleaned.find("{"), cleaned.rfind("}")
            if left >= 0 and right > left:
                candidates.append(cleaned[left:right + 1])
            for candidate in candidates:
                try:
                    parsed = json.loads(candidate)
                except Exception:
                    continue
                if isinstance(parsed, Mapping):
                    return parsed, candidate
            return None, cleaned

        def inspect_payload(payload: Any) -> dict[str, Any]:
            content = ""
            reasoning = ""
            finish_reason = None
            if isinstance(payload, Mapping) and isinstance(payload.get("choices"), list) and payload["choices"]:
                first = payload["choices"][0]
                if isinstance(first, Mapping):
                    finish_reason = first.get("finish_reason")
                    message = first.get("message")
                    if isinstance(message, Mapping):
                        if isinstance(message.get("content"), str):
                            content = str(message["content"]).strip()
                        if isinstance(message.get("reasoning_content"), str):
                            reasoning = str(message["reasoning_content"]).strip()
            parsed, parsed_text = parse_mapping(content)
            return {
                "content": content,
                "reasoning": reasoning,
                "finish_reason": finish_reason,
                "parsed": parsed,
                "parsed_text": parsed_text,
                "usage": payload.get("usage") if isinstance(payload, Mapping) else None,
                "reasoning_budget_exhausted": bool(not content and reasoning and finish_reason == "length"),
            }

        def send(body: Mapping[str, Any], transport: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
            try:
                status_code, payload, latency_ms = _http_json(
                    url, method="POST", headers=headers, body=body,
                    timeout_s=timeout_s, max_bytes=1048576,
                )
                info = inspect_payload(payload)
                attempt = {
                    "transport": transport,
                    "http_status": status_code,
                    "latency_ms": latency_ms,
                    "max_tokens": body.get("max_tokens"),
                    "finish_reason": info["finish_reason"],
                    "json_output": isinstance(info["parsed"], Mapping),
                    "reasoning_budget_exhausted": info["reasoning_budget_exhausted"],
                    "response_excerpt": info["content"][:500],
                    "reasoning_excerpt": info["reasoning"][:500],
                    "usage": info["usage"],
                }
                attempts.append(attempt)
                return info, attempt
            except Exception as exc:
                status, reason, details = _diagnostic_error(exc)
                attempt = {"transport": transport, "status": status, "reason": reason, "max_tokens": body.get("max_tokens"), **details}
                attempts.append(attempt)
                return None, attempt

        probe_schema = {
            "type": "json_schema",
            "json_schema": {
                "name": "sat_sim_provider_probe",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "const": "ok"},
                        "purpose": {"type": "string", "const": "sat-sim-provider-probe"},
                    },
                    "required": ["status", "purpose"],
                    "additionalProperties": False,
                },
            },
        }
        base_body: dict[str, Any] = {
            "model": profile.model,
            "messages": [
                {"role": "system", "content": "Return one valid JSON object only. Do not include reasoning or Markdown fences."},
                {"role": "user", "content": 'Return exactly {"status":"ok","purpose":"sat-sim-provider-probe"}.'},
            ],
            "stream": False,
            "temperature": 0,
            "max_tokens": 512,
        }
        primary_body = dict(base_body)
        if profile.provider_id == "local-lmstudio" or profile.structured_output == "json_schema":
            primary_body["response_format"] = probe_schema
            primary_transport = "json_schema"
        elif profile.structured_output == "json_object":
            primary_body["response_format"] = {"type": "json_object"}
            primary_transport = "json_object"
        else:
            primary_transport = "prompt_only_json"

        info, attempt = send(primary_body, primary_transport)
        if info is not None and isinstance(info["parsed"], Mapping):
            return {
                "provider_id": provider_id, "ok": True, "status": "READY",
                "reason": "最小结构化推理通过。", "endpoint": url, "model": profile.model,
                "http_status": attempt.get("http_status"), "latency_ms": attempt.get("latency_ms"),
                "json_output": True, "taskspec_draft_capable": True,
                "structured_output_transport": primary_transport,
                "response_excerpt": info["parsed_text"][:500], "usage": info["usage"],
                "attempts": attempts, "secret_values_exposed": False,
            }

        fallback_needed = info is None and attempt.get("http_status") in {400, 422}
        fallback_needed = fallback_needed or (info is not None and not isinstance(info["parsed"], Mapping))
        fallback_body = dict(base_body)
        fallback_transport = "prompt_only_json"
        if fallback_needed:
            info, attempt = send(fallback_body, fallback_transport)
            if info is not None and isinstance(info["parsed"], Mapping):
                return {
                    "provider_id": provider_id, "ok": True, "status": "READY",
                    "reason": "兼容提示词模式的 JSON 推理通过。", "endpoint": url, "model": profile.model,
                    "http_status": attempt.get("http_status"), "latency_ms": attempt.get("latency_ms"),
                    "json_output": True, "taskspec_draft_capable": True,
                    "structured_output_transport": fallback_transport,
                    "response_excerpt": info["parsed_text"][:500], "usage": info["usage"],
                    "attempts": attempts, "secret_values_exposed": False,
                }

        if info is not None and info.get("reasoning_budget_exhausted"):
            expanded_body = dict(fallback_body if fallback_needed else primary_body)
            expanded_body["max_tokens"] = 1536
            info, attempt = send(expanded_body, f"{fallback_transport if fallback_needed else primary_transport}_expanded_budget")
            if info is not None and isinstance(info["parsed"], Mapping):
                return {
                    "provider_id": provider_id, "ok": True, "status": "READY",
                    "reason": "推理型模型扩大输出预算后，结构化推理通过。", "endpoint": url, "model": profile.model,
                    "http_status": attempt.get("http_status"), "latency_ms": attempt.get("latency_ms"),
                    "json_output": True, "taskspec_draft_capable": True,
                    "structured_output_transport": attempt.get("transport"),
                    "response_excerpt": info["parsed_text"][:500], "usage": info["usage"],
                    "attempts": attempts, "secret_values_exposed": False,
                }

        if profile.provider_id == "local-ollama":
            native_url = _ollama_root(profile.base_url) + "/api/chat"
            native_body = {
                "model": profile.model,
                "messages": base_body["messages"],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            }
            try:
                status_code, payload, latency_ms = _http_json(
                    native_url, method="POST", headers=headers, body=native_body,
                    timeout_s=timeout_s, max_bytes=1048576,
                )
                message = payload.get("message") if isinstance(payload, Mapping) else None
                content = str(message.get("content") or "").strip() if isinstance(message, Mapping) else ""
                parsed, parsed_text = parse_mapping(content)
                json_ok = isinstance(parsed, Mapping)
                attempts.append({"transport": "ollama_native_json", "http_status": status_code, "latency_ms": latency_ms, "json_output": json_ok})
                return {
                    "provider_id": provider_id, "ok": json_ok,
                    "status": "READY" if json_ok else "INVALID_STRUCTURED_OUTPUT",
                    "reason": "Ollama 原生接口结构化推理通过。" if json_ok else "Ollama 可响应，但未返回可解析 JSON 对象。",
                    "endpoint": native_url, "model": profile.model, "http_status": status_code,
                    "latency_ms": latency_ms, "json_output": json_ok,
                    "taskspec_draft_capable": json_ok, "response_excerpt": parsed_text[:500],
                    "attempts": attempts, "secret_values_exposed": False,
                }
            except Exception as native_exc:
                status, reason, details = _diagnostic_error(native_exc)
                attempts.append({"transport": "ollama_native_json", "status": status, "reason": reason, **details})

        last_info = info or {}
        exhausted = bool(last_info.get("reasoning_budget_exhausted"))
        return {
            "provider_id": provider_id,
            "ok": False,
            "status": "REASONING_BUDGET_EXHAUSTED" if exhausted else "INVALID_STRUCTURED_OUTPUT",
            "reason": (
                "模型把输出预算全部用于推理，尚未生成最终 JSON；已自动扩大预算重试但仍未完成。请提高模型服务的输出上限，或关闭/降低思考模式。"
                if exhausted else "服务可连接，但完整测试未获得可解析的最终 JSON 对象。"
            ),
            "endpoint": url,
            "model": profile.model,
            "json_output": False,
            "taskspec_draft_capable": False,
            "finish_reason": last_info.get("finish_reason"),
            "response_excerpt": str(last_info.get("content") or "")[:500],
            "reasoning_excerpt": str(last_info.get("reasoning") or "")[:500],
            "usage": last_info.get("usage"),
            "attempts": attempts,
            "secret_values_exposed": False,
        }

    def catalog(self, *, live_probe: bool = False) -> dict[str, Any]:
        providers = []
        for profile in self.list():
            readiness = self.readiness(profile.provider_id, live_probe=live_probe)
            providers.append({**profile.to_dict(), "readiness": readiness.to_dict()})
        return {
            "registry_version": MODEL_PROVIDER_REGISTRY_VERSION,
            "count": len(providers),
            "providers": providers,
            "authority_boundary": "Providers draft TaskSpecs only; deterministic registries and validators remain authoritative.",
        }

    def select(
        self,
        *,
        input_kind: str,
        request_text: str,
        routing_mode: RoutingMode = "auto",
        provider_id: str | None = None,
    ) -> ProviderSelection:
        requested = provider_id
        fallback = self.get("local-template")
        fallback_used = False
        if provider_id:
            selected = self.get(provider_id)
            ready = self.readiness(provider_id).ready
            if not ready:
                selected = fallback
                fallback_used = True
                reason = f"requested provider {provider_id} unavailable; deterministic local fallback selected"
            else:
                reason = "explicit provider selected"
        else:
            candidates = [profile for profile in self.list(enabled_only=True) if self.readiness(profile.provider_id).ready]
            if routing_mode == "local":
                candidates = [item for item in candidates if item.location in {"local", "deterministic"}]
            elif routing_mode == "remote":
                candidates = [item for item in candidates if item.location == "remote"]
            score_route = route_model(
                input_kind=input_kind,  # type: ignore[arg-type]
                request_text=request_text,
                config=ModelRouteConfig(local_backend="template"),
            )
            wants_l2 = score_route.complexity_score >= 5
            if routing_mode == "remote" or (routing_mode == "auto" and wants_l2):
                preferred = [item for item in candidates if item.tier == "L2"]
            else:
                preferred = [item for item in candidates if item.location != "remote" and item.tier in {"L0", "L1"}]
            selected = (preferred or candidates or [fallback])[0]
            fallback_used = selected.provider_id == fallback.provider_id and (routing_mode == "remote" or wants_l2)
            reason = "complex request routed to configured L2 provider" if selected.tier == "L2" else "local deterministic provider selected"
            if fallback_used:
                reason = "requested routing tier unavailable; deterministic local fallback selected"

        force_tier = selected.tier
        route = route_model(
            input_kind=input_kind,  # type: ignore[arg-type]
            request_text=request_text,
            config=ModelRouteConfig(
                local_backend=selected.backend if selected.location != "remote" else "template",
                remote_backend=selected.backend if selected.location == "remote" or selected.tier == "L2" else None,
                remote_model=selected.model,
                remote_base_url=selected.base_url,
                remote_command=selected.command,
                force_tier=force_tier,
            ),
        )
        route = replace(
            route,
            backend=selected.backend,
            tier=selected.tier,
            provider_id=selected.provider_id,
            provider_location=selected.location,
            fallback_backend=fallback.backend if fallback_used else route.fallback_backend,
            remote_available=selected.location == "remote" and self.readiness(selected.provider_id).ready,
        )
        return ProviderSelection(selected, route, routing_mode, requested, fallback_used, reason)


__all__ = [
    "MODEL_PROVIDER_REGISTRY_VERSION",
    "ModelProviderProfile",
    "ModelProviderRegistry",
    "ProviderReadiness",
    "ProviderSelection",
    "RoutingMode",
    "builtin_provider_profiles",
]
