"""Opt-in REST and WebSocket surface for interactive simulation sessions."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
import time
from collections import deque
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from sat_sim.security import AuditLogger, AuthManager, AuthPrincipal

from .manager import InteractiveSessionManager
from .command_catalog import CommandCatalog
from .models import ActorRole, InteractiveSessionSpec, StreamGap, Telecommand
from .runtime_factory import build_persistent_runtime

MAX_WEBSOCKET_MESSAGE_BYTES = 65_536
MAX_WEBSOCKET_MESSAGES_PER_SECOND = 20


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SessionControlBody(_Strict):
    action: Literal["start", "pause", "resume", "step", "rate", "stop", "abort", "heartbeat"]
    quanta: int = Field(default=1, ge=1, le=10_000)
    rate: float | None = None


def _principal_role(principal: AuthPrincipal, requested: ActorRole) -> ActorRole:
    if not principal.has_any_role({requested.value}):
        raise PermissionError("COMMAND_ROLE_DENIED")
    if "admin" in principal.roles:
        return ActorRole.ADMIN
    if requested is ActorRole.FAULT_OPERATOR and "fault_operator" in principal.roles:
        return ActorRole.FAULT_OPERATOR
    return ActorRole.OPERATOR


def _audit(audit: AuditLogger, event: str, principal: AuthPrincipal, **details: Any) -> None:
    audit.log({
        "event": event,
        "principal_id": principal.principal_id,
        **details,
    })


def create_interactive_router(
    manager: InteractiveSessionManager,
    auth_manager: AuthManager,
    audit_logger: AuditLogger,
):
    try:
        from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
        globals()["Request"] = Request
        globals()["WebSocket"] = WebSocket
    except ImportError as exc:  # pragma: no cover - optional API dependency
        raise RuntimeError("FastAPI is required for the interactive API") from exc

    router = APIRouter(prefix="/interactive", tags=["interactive"])
    command_catalog = CommandCatalog.default()
    command_rate_windows: dict[tuple[str, str], deque[float]] = {}
    command_rate_lock = threading.Lock()

    def admit_command(principal: AuthPrincipal, session_id: str) -> bool:
        now = time.monotonic()
        key = (principal.principal_id, session_id)
        with command_rate_lock:
            window = command_rate_windows.setdefault(key, deque())
            while window and now - window[0] >= 1.0:
                window.popleft()
            if len(window) >= MAX_WEBSOCKET_MESSAGES_PER_SECOND:
                return False
            window.append(now)
            return True

    def get_session(session_id: str):
        try:
            return manager.get(session_id)
        except KeyError as exc:
            raise HTTPException(404, detail={"reason_code": "INTERACTIVE_SESSION_NOT_FOUND"}) from exc

    @router.post("/sessions", status_code=201)
    def create_session(spec: InteractiveSessionSpec, request: Request):
        principal = request.state.principal
        try:
            runtime, end_time_s = build_persistent_runtime(spec)
            session = manager.create(spec, runtime, end_time_s=end_time_s)
            session.prepare()
        except ValueError as exc:
            raise HTTPException(422, detail={"reason_code": str(exc)}) from exc
        except RuntimeError as exc:
            raise HTTPException(409, detail={"reason_code": str(exc)}) from exc
        _audit(audit_logger, "interactive_session_created", principal, session_id=spec.session_id)
        return session.snapshot()

    @router.get("/sessions")
    def list_sessions():
        return {"sessions": manager.snapshots()}

    @router.get("/commands/catalog")
    def read_command_catalog():
        return {"schema_version": "interactive-command-catalog.v1", "commands": command_catalog.public_contracts()}

    @router.get("/sessions/{session_id}")
    def read_session(session_id: str):
        return get_session(session_id).snapshot()

    @router.post("/sessions/{session_id}/control")
    def control_session(session_id: str, body: SessionControlBody, request: Request):
        session = get_session(session_id)
        principal = request.state.principal
        if body.action == "abort" and not principal.has_any_role({"admin"}):
            raise HTTPException(403, detail={"reason_code": "SESSION_ABORT_ROLE_DENIED"})
        try:
            if body.action == "start":
                session.start()
            elif body.action == "pause":
                session.pause()
            elif body.action == "resume":
                session.resume()
            elif body.action == "step":
                session.step(body.quanta)
            elif body.action == "rate":
                if body.rate is None:
                    raise ValueError("rate is required for rate action")
                session.set_rate(body.rate)
            elif body.action == "stop":
                session.stop()
            elif body.action == "abort":
                session.abort()
            else:
                session.heartbeat()
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(409, detail={"reason_code": str(exc)}) from exc
        _audit(
            audit_logger, "interactive_session_control", principal,
            session_id=session_id, action=body.action,
        )
        return session.snapshot()

    @router.post("/sessions/{session_id}/commands", status_code=202)
    def submit_command(session_id: str, command: Telecommand, request: Request):
        session = get_session(session_id)
        principal = request.state.principal
        if not admit_command(principal, session_id):
            _audit(
                audit_logger, "interactive_command_denied", principal,
                session_id=session_id, command_id=command.command_id,
                reason_code="COMMAND_RATE_EXCEEDED",
            )
            raise HTTPException(429, detail={"reason_code": "COMMAND_RATE_EXCEEDED"})
        if command.session_id != session_id:
            raise HTTPException(422, detail={"reason_code": "SESSION_MISMATCH"})
        try:
            actor_role = _principal_role(principal, command.actor_role)
        except PermissionError as exc:
            _audit(
                audit_logger, "interactive_command_denied", principal,
                session_id=session_id, command_id=command.command_id, reason_code=str(exc),
            )
            raise HTTPException(403, detail={"reason_code": str(exc)}) from exc
        trusted_command = command.model_copy(update={
            "actor_id": principal.principal_id,
            "actor_role": actor_role,
        })
        ack = session.submit_command(trusted_command)
        _audit(
            audit_logger, "interactive_command_received", principal,
            session_id=session_id, command_id=command.command_id, ack_state=ack.state,
            reason_code=ack.reason_code,
        )
        return ack

    @router.get("/sessions/{session_id}/commands")
    def read_commands(session_id: str, command_id: str | None = None):
        return {"acks": get_session(session_id).command_acks(command_id)}

    @router.get("/sessions/{session_id}/events")
    def read_session_events(session_id: str, after_sequence: int = -1):
        return {"events": get_session(session_id).session_events(after_sequence)}

    @router.get("/sessions/{session_id}/telemetry/{stream}")
    def read_telemetry(session_id: str, stream: str, after_sequence: int = -1):
        session = get_session(session_id)
        if stream not in session.allowed_telemetry_streams:
            raise HTTPException(404, detail={"reason_code": "TELEMETRY_STREAM_NOT_ALLOWED"})
        replay = session.telemetry.read_after(stream, after_sequence)
        gap = None
        if replay.gap and replay.oldest_sequence is not None:
            gap = StreamGap(
                session_id=session_id,
                stream=stream,
                after_sequence=after_sequence,
                next_sequence=replay.oldest_sequence,
                dropped_count=replay.oldest_sequence - after_sequence - 1,
                reason_code="BUFFER_EVICTED",
            )
        return {
            "frames": replay.frames,
            "gap": gap,
            "oldest_sequence": replay.oldest_sequence,
            "latest_sequence": replay.latest_sequence,
        }

    def authenticate_websocket(websocket: WebSocket) -> tuple[AuthPrincipal | None, str | None]:
        principal = auth_manager.authenticate_header(websocket.headers.get("authorization"))
        selected_protocol = None
        raw_protocols = websocket.headers.get("sec-websocket-protocol", "")
        protocols = [item.strip() for item in raw_protocols.split(",") if item.strip()]
        if "sat-sim-v1" in protocols:
            selected_protocol = "sat-sim-v1"
        if principal is None and auth_manager.enabled:
            encoded = next((item.removeprefix("sat-auth.") for item in protocols if item.startswith("sat-auth.")), None)
            if encoded and len(encoded) <= 8192:
                try:
                    padding = "=" * (-len(encoded) % 4)
                    token = base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
                    principal = auth_manager.authenticate_header(f"Bearer {token}")
                except (ValueError, UnicodeDecodeError):
                    principal = None
        if principal is None or not principal.has_any_role({"viewer", "operator", "admin"}):
            return None, selected_protocol
        configured = os.getenv("SAT_SIM_INTERACTIVE_ALLOWED_ORIGINS", "").strip()
        origin = websocket.headers.get("origin")
        if configured and origin:
            allowed = {item.strip() for item in configured.split(",") if item.strip()}
            if origin not in allowed:
                return None, selected_protocol
        return principal, selected_protocol

    async def receive_payload(websocket: WebSocket, timeout_s: float | None = None) -> dict[str, Any] | None:
        try:
            text = await asyncio.wait_for(websocket.receive_text(), timeout_s) if timeout_s else await websocket.receive_text()
        except TimeoutError:
            return None
        if len(text.encode("utf-8")) > MAX_WEBSOCKET_MESSAGE_BYTES:
            await websocket.close(code=1009, reason="MESSAGE_TOO_LARGE")
            raise WebSocketDisconnect(code=1009)
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            await websocket.close(code=1003, reason="INVALID_JSON")
            raise WebSocketDisconnect(code=1003) from None
        if not isinstance(payload, dict):
            await websocket.close(code=1003, reason="MESSAGE_MUST_BE_OBJECT")
            raise WebSocketDisconnect(code=1003)
        return payload

    @router.websocket("/sessions/{session_id}/telemetry")
    async def telemetry_websocket(websocket: WebSocket, session_id: str):
        principal, selected_protocol = authenticate_websocket(websocket)
        if principal is None:
            await websocket.close(code=4401, reason="AUTHENTICATION_OR_ORIGIN_DENIED")
            return
        try:
            session = manager.get(session_id)
        except KeyError:
            await websocket.close(code=4404, reason="INTERACTIVE_SESSION_NOT_FOUND")
            return
        await websocket.accept(subprotocol=selected_protocol)
        subscription = None
        message_times: deque[float] = deque()
        try:
            first = await receive_payload(websocket)
            if first is None or first.get("action") != "subscribe":
                await websocket.close(code=1008, reason="SUBSCRIBE_REQUIRED")
                return

            async def replace_subscription(payload: dict[str, Any]) -> None:
                nonlocal subscription
                streams = tuple(str(item) for item in payload.get("streams", ()))
                if not streams or any(stream not in session.allowed_telemetry_streams for stream in streams):
                    await websocket.close(code=1008, reason="TELEMETRY_STREAM_NOT_ALLOWED")
                    raise WebSocketDisconnect(code=1008)
                after_raw = payload.get("after_sequences") or {}
                if not isinstance(after_raw, dict):
                    await websocket.close(code=1008, reason="INVALID_AFTER_SEQUENCES")
                    raise WebSocketDisconnect(code=1008)
                if subscription is not None:
                    session.telemetry.unsubscribe(subscription.subscription_id)
                subscription = session.telemetry.subscribe(
                    streams,
                    capacity=min(max(int(payload.get("capacity", 256)), 1), 4096),
                    overflow_policy=str(payload.get("overflow_policy", "drop_oldest")),
                    after_sequences={str(key): int(value) for key, value in after_raw.items()},
                )
                await websocket.send_json({
                    "type": "subscription_ack",
                    "subscription_id": subscription.subscription_id,
                    "streams": sorted(subscription.streams),
                })

            await replace_subscription(first)
            _audit(audit_logger, "interactive_telemetry_subscribed", principal, session_id=session_id)
            event_sequence = int(first.get("after_event_sequence", -1))
            heartbeat_at = time.monotonic() + session.spec.heartbeat_interval_s
            while subscription is not None:
                for event in session.session_events(event_sequence):
                    await websocket.send_json({"type": "session_event", "payload": event.model_dump(mode="json")})
                    event_sequence = event.sequence
                for item in subscription.poll(max_items=100):
                    message_type = "gap" if isinstance(item, StreamGap) else "telemetry"
                    await websocket.send_json({"type": message_type, "payload": item.model_dump(mode="json")})
                if subscription.closed:
                    await websocket.close(code=1013, reason=subscription.close_reason or "SUBSCRIBER_CLOSED")
                    return
                now = time.monotonic()
                if now >= heartbeat_at:
                    await websocket.send_json({"type": "heartbeat", "payload": session.snapshot().model_dump(mode="json")})
                    heartbeat_at = now + session.spec.heartbeat_interval_s
                payload = await receive_payload(websocket, 0.05)
                if payload is None:
                    continue
                message_times.append(now)
                while message_times and now - message_times[0] >= 1.0:
                    message_times.popleft()
                if len(message_times) > MAX_WEBSOCKET_MESSAGES_PER_SECOND:
                    await websocket.close(code=1008, reason="MESSAGE_RATE_EXCEEDED")
                    return
                action = payload.get("action")
                if action == "heartbeat":
                    await websocket.send_json({"type": "heartbeat_ack", "payload": session.heartbeat().model_dump(mode="json")})
                elif action == "subscribe":
                    await replace_subscription(payload)
                elif action == "unsubscribe":
                    await websocket.send_json({"type": "unsubscribe_ack"})
                    return
                else:
                    await websocket.close(code=1008, reason="UNSUPPORTED_ACTION")
                    return
        except (WebSocketDisconnect, ValueError):
            return
        finally:
            if subscription is not None:
                session.telemetry.unsubscribe(subscription.subscription_id)

    return router


__all__ = ["create_interactive_router"]
