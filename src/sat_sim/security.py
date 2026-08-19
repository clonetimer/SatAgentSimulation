"""Token authentication, role authorization, and append-only API audit logging.

Authentication is intentionally kept simple and deployable without a
separate identity provider.  Tokens are supplied through environment variables
or pre-computed SHA-256 digests; plaintext token values are never returned by
catalog or status APIs.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

AUTH_CONFIG_VERSION = "auth-config.v1"
LEGACY_AUTH_CONFIG_VERSIONS = {"v38d.auth-config.v1"}
AUDIT_LOG_VERSION = "api-audit.v1"
AUTH_MODES = {"disabled", "token"}
KNOWN_ROLES = {"viewer", "operator", "fault_operator", "admin", "worker"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_sha256(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthPrincipal:
    principal_id: str
    roles: frozenset[str]
    token_id: str | None = None
    worker_id: str | None = None
    authenticated: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def has_any_role(self, roles: Iterable[str]) -> bool:
        required = {str(item) for item in roles}
        if "admin" in self.roles:
            return True
        if "operator" in self.roles and "viewer" in required:
            return True
        if "fault_operator" in self.roles and required & {"viewer", "operator", "fault_operator"}:
            return True
        return bool(self.roles & required)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "principal_id": self.principal_id,
            "roles": sorted(self.roles),
            "token_id": self.token_id,
            "worker_id": self.worker_id,
            "authenticated": self.authenticated,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class AuthTokenRecord:
    token_id: str
    principal_id: str
    roles: frozenset[str]
    token_sha256: str
    worker_id: str | None = None
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def principal(self) -> AuthPrincipal:
        return AuthPrincipal(
            principal_id=self.principal_id,
            roles=self.roles,
            token_id=self.token_id,
            worker_id=self.worker_id,
            authenticated=True,
            metadata=dict(self.metadata),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "token_id": self.token_id,
            "principal_id": self.principal_id,
            "roles": sorted(self.roles),
            "worker_id": self.worker_id,
            "enabled": self.enabled,
            "secret_values_exposed": False,
            "metadata": dict(self.metadata),
        }


class AuthManager:
    def __init__(
        self,
        *,
        mode: str = "disabled",
        records: Iterable[AuthTokenRecord] = (),
        config_source: str | None = None,
    ) -> None:
        normalized = str(mode).strip().lower()
        if normalized not in AUTH_MODES:
            raise ValueError(f"unsupported auth mode: {mode}")
        self.mode = normalized
        self._records = tuple(item for item in records if item.enabled)
        self.config_source = config_source
        if self.mode == "token" and not self._records:
            raise ValueError("token auth mode requires at least one enabled token record")

    @classmethod
    def from_env(
        cls,
        *,
        mode: str | None = None,
        config_path: str | Path | None = None,
    ) -> "AuthManager":
        resolved_mode = str(mode or os.getenv("SAT_SIM_AUTH_MODE", "disabled")).strip().lower()
        source = config_path or os.getenv("SAT_SIM_AUTH_TOKENS_FILE")
        if resolved_mode == "disabled":
            return cls(mode="disabled", config_source=str(source) if source else None)
        if not source:
            raise ValueError("SAT_SIM_AUTH_TOKENS_FILE is required when SAT_SIM_AUTH_MODE=token")
        path = Path(source).expanduser().resolve()
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError("auth token config must be a JSON object")
        if payload.get("schema_version") not in {None, AUTH_CONFIG_VERSION, *LEGACY_AUTH_CONFIG_VERSIONS}:
            raise ValueError(f"unsupported auth config schema: {payload.get('schema_version')}")
        rows = payload.get("tokens")
        if not isinstance(rows, list):
            raise ValueError("auth token config requires a tokens array")
        records: list[AuthTokenRecord] = []
        seen_ids: set[str] = set()
        for index, raw in enumerate(rows):
            if not isinstance(raw, Mapping):
                raise ValueError(f"tokens[{index}] must be an object")
            token_id = str(raw.get("token_id") or f"token-{index + 1}")
            if token_id in seen_ids:
                raise ValueError(f"duplicate token_id: {token_id}")
            seen_ids.add(token_id)
            principal_id = str(raw.get("principal_id") or token_id)
            roles = frozenset(str(item).strip().lower() for item in raw.get("roles", []) if str(item).strip())
            unknown = roles - KNOWN_ROLES
            if not roles or unknown:
                raise ValueError(f"invalid roles for {token_id}: {sorted(unknown) if unknown else 'empty'}")
            token_env = str(raw.get("token_env") or "").strip() or None
            digest = str(raw.get("token_sha256") or "").strip().lower() or None
            if token_env:
                token_value = os.getenv(token_env)
                if not token_value:
                    if bool(raw.get("enabled", True)):
                        raise ValueError(f"environment variable {token_env} is not configured for {token_id}")
                    continue
                digest = token_sha256(token_value)
            if not digest or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise ValueError(f"token {token_id} requires token_env or a valid token_sha256")
            worker_id = str(raw.get("worker_id") or "").strip() or None
            if "worker" in roles and not worker_id:
                worker_id = principal_id
            records.append(AuthTokenRecord(
                token_id=token_id,
                principal_id=principal_id,
                roles=roles,
                token_sha256=digest,
                worker_id=worker_id,
                enabled=bool(raw.get("enabled", True)),
                metadata=dict(raw.get("metadata") or {}),
            ))
        return cls(mode=resolved_mode, records=records, config_source=str(path))

    @property
    def enabled(self) -> bool:
        return self.mode == "token"

    def local_development_principal(self) -> AuthPrincipal:
        return AuthPrincipal(
            principal_id="local-development",
            roles=frozenset(KNOWN_ROLES),
            token_id=None,
            worker_id=None,
            authenticated=False,
            metadata={"auth_mode": "disabled"},
        )

    def authenticate_header(self, authorization: str | None) -> AuthPrincipal | None:
        if not self.enabled:
            return self.local_development_principal()
        if not authorization:
            return None
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() != "bearer" or not value.strip():
            return None
        digest = token_sha256(value.strip())
        for record in self._records:
            if hmac.compare_digest(digest, record.token_sha256):
                return record.principal()
        return None

    def catalog(self) -> dict[str, Any]:
        return {
            "schema_version": AUTH_CONFIG_VERSION,
            "mode": self.mode,
            "enabled": self.enabled,
            "token_count": len(self._records),
            "tokens": [record.to_public_dict() for record in self._records],
            "config_source": self.config_source,
            "secret_values_exposed": False,
        }


class AuditLogger:
    """Append-only JSONL audit sink.

    The logger records request metadata and authorization outcomes but never
    request bodies, bearer tokens, API keys, or model prompts.
    """

    def __init__(self, path: str | Path | None) -> None:
        self.path = Path(path).resolve() if path else None
        self._lock = threading.Lock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, event: Mapping[str, Any]) -> None:
        if self.path is None:
            return
        payload = {
            "schema_version": AUDIT_LOG_VERSION,
            "occurred_at": utc_now(),
            **dict(event),
        }
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())


def required_roles_for_request(method: str, path: str) -> frozenset[str]:
    """Return the minimum role set for a protected path.

    Public-path handling is intentionally outside this function so deployments
    can keep health checks and static assets unauthenticated.
    """

    normalized_method = method.upper()
    if path.startswith("/remote-workers"):
        return frozenset({"worker", "admin"})
    if path in {"/auth/config", "/health/details"}:
        return frozenset({"admin"})
    if path.startswith("/diagnostics") or path.startswith("/logs"):
        return frozenset({"operator", "admin"})
    if path.startswith("/queue") or path.startswith("/workers"):
        return frozenset({"operator", "admin"})
    if path.startswith("/experience-lessons") and normalized_method not in {
        "GET",
        "HEAD",
        "OPTIONS",
    }:
        if any(
            token in path
            for token in ("/review", "/revoke", "/snapshots", "/reuse", "/rollback")
        ):
            return frozenset({"admin"})
        return frozenset({"operator", "admin"})
    if normalized_method in {"GET", "HEAD", "OPTIONS"}:
        return frozenset({"viewer", "operator", "admin"})
    return frozenset({"operator", "admin"})


__all__ = [
    "AUDIT_LOG_VERSION",
    "AUTH_CONFIG_VERSION",
    "AUTH_MODES",
    "AuthManager",
    "AuthPrincipal",
    "AuthTokenRecord",
    "AuditLogger",
    "KNOWN_ROLES",
    "required_roles_for_request",
    "token_sha256",
    "utc_now",
]
