from __future__ import annotations

import hashlib
import logging

import anyio
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from egregore.application.chat_interpreter import ChatContext, execute_message
from egregore.application.ws_chat_transport_mapper import (
    dumps_chat_error,
    dumps_chat_result,
)
from egregore.http_api.http.middleware.api_key_middleware import (
    _API_KEYS,
)
from egregore.infrastructure.persistence.user_repository import (
    SQLiteUserRepository,
    get_default_user_repository,
)
from egregore.models.user import UserIdentity

logger = logging.getLogger(__name__)
router = APIRouter()


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _context_from_websocket(websocket: WebSocket, session_id: str) -> ChatContext:
    """Build a ChatContext from the WebSocket connection and api_key cookie."""
    api_key = websocket.cookies.get("api_key", "") or websocket.query_params.get(
        "api_key", ""
    )
    user_id = "anonymous"
    role = "reader"
    identity: UserIdentity | None = None
    caller_identity_token = ""

    if api_key:
        caller_identity_token = f"api_key:{api_key}"
        try:
            repo = get_default_user_repository()
            if isinstance(repo, SQLiteUserRepository):
                repo.bootstrap_admin_if_needed(tenant_id="default", api_keys=_API_KEYS)
            identity = repo.resolve_identity(_hash_key(api_key), tenant_id="default")
        except Exception:  # noqa: S110
            identity = None

    if identity is not None:
        user_id = identity.user_id
        role = identity.roles[0] if identity.roles else "reader"
    else:
        # Legacy fallback if persistence is unavailable.
        from egregore.http_api.http.middleware.api_key_middleware import (
            get_identity_for_key,
            is_valid_api_key,
        )

        if api_key and is_valid_api_key(api_key):
            env_identity = get_identity_for_key(api_key)
            if env_identity:
                _tenant, user_id, role = env_identity

    inference_service = getattr(websocket.app.state, "inference_service", None)
    agent_registry = getattr(websocket.app.state, "agent_registry", None)
    job_runtime = getattr(websocket.app.state, "job_runtime", None)
    return ChatContext(
        session_id=session_id,
        user_id=user_id,
        role=role,
        identity=identity,
        env={
            "inference_service": inference_service,
            "agent_registry": agent_registry,
            "job_runtime": job_runtime,
            "caller_identity_token": caller_identity_token,
        },
    )


@router.websocket("/ws/chat/{session_id}")
async def chat_ws(websocket: WebSocket, session_id: str) -> None:
    await websocket.accept()
    context = _context_from_websocket(websocket, session_id)

    # Fail-closed: require a valid API key (via cookie or query param).
    if context.identity is None and context.role not in {"admin", "operator", "user"}:
        await websocket.close(code=1008, reason="Authentication required")
        return

    try:
        while True:
            message = await websocket.receive_text()
            try:
                # chat interpreter is synchronous and may load/run a GGUF model;
                # run it in a thread so the websocket can heartbeat/close cleanly.
                result = await anyio.to_thread.run_sync(
                    execute_message, message, context
                )
                await websocket.send_text(dumps_chat_result(payload=result))
            except Exception as exc:
                logger.exception("WS chat execute_message failed")
                error_text = str(exc) or f"{type(exc).__name__}"
                await websocket.send_text(
                    dumps_chat_error(command="unknown", error=error_text)
                )
    except WebSocketDisconnect:
        return
    except Exception as e:
        await websocket.send_text(dumps_chat_error(command="unknown", error=str(e)))
        await websocket.close()
