#!/usr/bin/env python3
"""Deploy the static frontend to veFaaS and expose it through APIG."""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path

from agentkit.auth._openapi import ApiError, OpenApiClient
from agentkit.auth.credential_hosting import ensure_gateway


ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
STATE_PATH = ROOT / ".vefaas" / "config.json"
REGION = "cn-beijing"
FUNCTION_NAME = "work-life-agent-frontend"
GATEWAY_NAME = "work-life-agent-gateway"
SERVICE_NAME = "work-life-agent-frontend"


def load_env() -> None:
    for raw_line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def zip_frontend() -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(FRONTEND.rglob("*")):
            if not path.is_file() or path.name == ".DS_Store":
                continue
            archive.write(path, path.relative_to(FRONTEND))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def find_named(items: list[dict], field: str, value: str) -> dict:
    return next((item for item in items if str(item.get(field) or "") == value), {})


def ensure_function(api: OpenApiClient) -> str:
    listed = api.call(
        "vefaas",
        "ListFunctions",
        "2021-03-03",
        {"PageNumber": 1, "PageSize": 100},
    )
    existing = find_named(listed.get("Items") or [], "Name", FUNCTION_NAME)
    source = zip_frontend()
    if existing:
        function_id = str(existing.get("Id") or "")
        api.call(
            "vefaas",
            "UpdateFunction",
            "2021-03-03",
            {
                "Id": function_id,
                "Command": "python3 static_server.py",
                "Port": 8000,
                "MemoryMB": 1024,
                "RequestTimeout": 120,
                "SourceType": "zip",
                "Source": source,
            },
        )
    else:
        created = api.call(
            "vefaas",
            "CreateFunction",
            "2021-03-03",
            {
                "Name": FUNCTION_NAME,
                "Runtime": "native-python3.12/v1",
                "Command": "python3 static_server.py",
                "Port": 8000,
                "MemoryMB": 1024,
                "RequestTimeout": 120,
                "SourceType": "zip",
                "Source": source,
                "Description": "Work Life Agent static frontend",
            },
        )
        function_id = str(created.get("Id") or "")
    if not function_id:
        raise RuntimeError("veFaaS did not return a function ID")
    return function_id


def release_function(api: OpenApiClient, function_id: str) -> int:
    released = api.call(
        "vefaas",
        "Release",
        "2021-03-03",
        {"FunctionId": function_id, "RevisionNumber": 0},
    )
    revision = int(released.get("NewRevisionNumber") or 0)
    for _ in range(60):
        status = api.call(
            "vefaas",
            "GetReleaseStatus",
            "2021-03-03",
            {"FunctionId": function_id},
        )
        state = str(status.get("Status") or "").lower()
        if state in {"done", "succeeded", "success"}:
            return int(status.get("StableRevisionNumber") or revision)
        if "fail" in state or state == "error":
            raise RuntimeError(
                f"veFaaS release failed: {status.get('StatusMessage') or state}"
            )
        time.sleep(5)
    raise TimeoutError("timed out waiting for veFaaS release")


def list_items(
    api: OpenApiClient, action: str, version: str, body: dict
) -> list[dict]:
    result = api.call("apig", action, version, body)
    return result.get("Items") or []


def ensure_public_route(
    api: OpenApiClient, gateway_id: str, function_id: str
) -> tuple[str, str, str]:
    upstream_name = f"{SERVICE_NAME}-upstream"
    upstream = find_named(
        list_items(
            api,
            "ListUpstreams",
            "2021-03-03",
            {"GatewayId": gateway_id, "PageNumber": 1, "PageSize": 100},
        ),
        "Name",
        upstream_name,
    )
    if not upstream:
        upstream = api.call(
            "apig",
            "CreateUpstream",
            "2021-03-03",
            {
                "GatewayId": gateway_id,
                "Name": upstream_name,
                "SourceType": "VeFaas",
                "Protocol": "HTTP",
                "UpstreamSpec": {"VeFaas": {"FunctionId": function_id}},
            },
        )
    upstream_id = str(upstream.get("Id") or "")

    service = find_named(
        list_items(
            api,
            "ListGatewayServices",
            "2021-03-03",
            {"GatewayId": gateway_id, "PageNumber": 1, "PageSize": 100},
        ),
        "ServiceName",
        SERVICE_NAME,
    )
    if not service:
        service = api.call(
            "apig",
            "CreateGatewayService",
            "2021-03-03",
            {
                "GatewayId": gateway_id,
                "ServiceName": SERVICE_NAME,
                "Protocol": ["HTTP", "HTTPS"],
                "DomainType": "DefaultDomain",
                "AuthSpec": {"Enable": False},
                "ServiceNetworkSpec": {
                    "EnablePublicNetwork": True,
                    "EnablePrivateNetwork": False,
                },
            },
        )
    service_id = str(service.get("Id") or "")

    route_name = f"{SERVICE_NAME}-route"
    route = find_named(
        list_items(
            api,
            "ListRoutes",
            "2022-11-12",
            {"ServiceId": service_id, "PageNumber": 1, "PageSize": 100},
        ),
        "Name",
        route_name,
    )
    if not route:
        route = api.call(
            "apig",
            "CreateRoute",
            "2022-11-12",
            {
                "Name": route_name,
                "ServiceId": service_id,
                "MatchRule": {
                    "Path": {"MatchType": "Prefix", "MatchContent": "/"},
                    "Method": ["GET", "HEAD"],
                },
                "UpstreamList": [{"UpstreamId": upstream_id, "Weight": 100}],
                "Enable": True,
                "Priority": 0,
            },
        )
    route_id = str(route.get("Id") or "")
    if not all((upstream_id, service_id, route_id)):
        raise RuntimeError("APIG did not return all expected resource IDs")
    return upstream_id, service_id, route_id


def main() -> int:
    load_env()
    account_id = os.environ.get("MUA_ACCOUNT_ID", "").strip()
    api = OpenApiClient(region=REGION, expect_account=account_id or None)

    print("Creating or updating veFaaS function...")
    function_id = ensure_function(api)
    revision = release_function(api, function_id)

    print("Creating or reusing APIG gateway...")
    gateway_id = ensure_gateway(api, name=GATEWAY_NAME, region=REGION)
    upstream_id, service_id, route_id = ensure_public_route(
        api, gateway_id, function_id
    )
    access_url = f"https://{service_id}.apigateway-{REGION}.volceapi.com/"

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(
            {
                "account_id": api.account_id,
                "region": REGION,
                "function_id": function_id,
                "revision": revision,
                "gateway_id": gateway_id,
                "upstream_id": upstream_id,
                "service_id": service_id,
                "route_id": route_id,
                "access_url": access_url,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"Frontend URL: {access_url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ApiError, OSError, RuntimeError, TimeoutError) as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
