"""Serve the work_life_agent as an AgentKit runtime."""

from __future__ import annotations

import os

from agentkit.apps import AgentkitAgentServerApp
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from veadk.memory.short_term_memory import ShortTermMemory

from assistant import root_agent
from assistant.user_registry import assert_run_owner
from assistant.tools import (
    ensure_user_cloud_phone,
    get_cloud_phone_stream_session,
    inspect_cloud_phone_run,
    provision_user_cloud_phone,
    start_bound_cloud_phone_task,
)
from auth import authenticated_user_id


short_term_memory = ShortTermMemory(backend="local")
server = AgentkitAgentServerApp(agent=root_agent, short_term_memory=short_term_memory)
agentkit_app = server.app
app = FastAPI(title="work_life_agent runtime")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        origin.strip()
        for origin in os.getenv(
            "CORS_ORIGINS", "http://127.0.0.1:5180,http://localhost:5180"
        ).split(",")
        if origin.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)


def _request_user_id(request: Request) -> str:
    return authenticated_user_id(request)


@app.middleware("http")
async def bind_agentkit_user_to_verified_jwt(request: Request, call_next):
    """Make AgentKit ToolContext.user_id equal the verified Identity `sub`."""
    if request.url.path == "/invoke" and request.method != "OPTIONS":
        user_id = authenticated_user_id(request)
        headers = [
            (key, value)
            for key, value in request.scope["headers"]
            if key.lower() != b"user_id"
        ]
        headers.append((b"user_id", user_id.encode("utf-8")))
        request.scope["headers"] = headers
    return await call_next(request)


@app.get("/api/me")
async def api_me(request: Request) -> dict[str, object]:
    user_id = _request_user_id(request)
    try:
        binding = ensure_user_cloud_phone(user_id)
    except Exception as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {"ok": True, "user_id": user_id, "binding": binding}


@app.post("/api/mua/stream-token")
async def api_mua_stream_token(request: Request) -> dict[str, object]:
    body = await request.json() if request.headers.get("content-length") not in {None, "0"} else {}
    user_id = _request_user_id(request)
    ensure_user_cloud_phone(user_id)
    ttl_seconds = int(body.get("ttl_seconds") or 600)
    result = get_cloud_phone_stream_session(user_id=user_id, ttl_seconds=ttl_seconds)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "failed to create stream token")
    return result


@app.get("/api/stream-session")
async def api_stream_session(request: Request) -> dict[str, object]:
    """Compatibility endpoint matching the standalone MUA stream viewer."""
    user_id = _request_user_id(request)
    ttl_seconds = int(request.query_params.get("ttl_seconds") or 600)
    result = get_cloud_phone_stream_session(user_id=user_id, ttl_seconds=ttl_seconds)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "failed to create stream session")
    return result


@app.post("/api/mua/task")
async def api_mua_task(request: Request) -> dict[str, object]:
    body = await request.json()
    user_id = _request_user_id(request)
    ensure_user_cloud_phone(user_id)
    result = start_bound_cloud_phone_task(
        user_id=user_id,
        prompt=str(body.get("prompt") or ""),
        wait=bool(body.get("wait") or False),
        max_step=int(body.get("max_step") or 100),
        timeout=int(body.get("timeout") or 600),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "failed to start MUA task")
    return result


@app.post("/api/mua/provision")
async def api_mua_provision(request: Request) -> dict[str, object]:
    body = await request.json() if request.headers.get("content-length") not in {None, "0"} else {}
    user_id = _request_user_id(request)
    result = provision_user_cloud_phone(
        user_id=user_id,
        pod_name=str(body.get("pod_name") or ""),
        configuration_code=str(body.get("configuration_code") or ""),
        dc=str(body.get("dc") or ""),
        data_size=str(body.get("data_size") or ""),
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "failed to provision cloud phone")
    return result


@app.get("/api/mua/task/{run_id}")
async def api_mua_task_status(run_id: str, request: Request) -> dict[str, object]:
    user_id = _request_user_id(request)
    try:
        assert_run_owner(run_id, user_id)
    except PermissionError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    result = inspect_cloud_phone_run(run_id=run_id)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("error") or "failed to inspect MUA task")
    return result


app.mount("/", agentkit_app)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
