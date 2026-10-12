#!/usr/bin/env python3
"""Deploy the frontend to veFaaS and expose it through APIG.

This script uses the official Volcengine Python SDK directly. It does not use
the veFaaS MCP or AgentKit's private OpenAPI helpers.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import volcenginesdkcore


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = ROOT / "frontend"
DEFAULT_STATE = ROOT / ".vefaas" / "config.json"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"environment file not found: {path}")
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


class VolcOpenAPI:
    def __init__(self, region: str) -> None:
        ak = os.getenv("VOLCENGINE_ACCESS_KEY") or os.getenv("VOLC_ACCESSKEY")
        sk = os.getenv("VOLCENGINE_SECRET_KEY") or os.getenv("VOLC_SECRETKEY")
        if not ak or not sk:
            raise RuntimeError("VOLCENGINE_ACCESS_KEY and VOLCENGINE_SECRET_KEY are required")

        config = volcenginesdkcore.Configuration()
        config.ak = ak
        config.sk = sk
        config.region = region
        config.auto_retry = False
        self.api = volcenginesdkcore.UniversalApi(
            volcenginesdkcore.ApiClient(config)
        )

    def call(
        self,
        service: str,
        action: str,
        version: str,
        body: dict[str, Any],
        *,
        method: str = "POST",
    ) -> dict[str, Any]:
        info = volcenginesdkcore.UniversalInfo(
            method=method,
            action=action,
            service=service,
            version=version,
            content_type="application/json",
        )
        payload = (
            volcenginesdkcore.Flatten(body).flat()
            if method.upper() == "GET"
            else body
        )
        result = self.api.do_call(info, payload)
        if not isinstance(result, dict):
            raise RuntimeError(f"{action} returned an invalid response")
        return result


def package_directory(source_dir: Path) -> str:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(source_dir)
            if path.name == ".DS_Store" or "__pycache__" in relative.parts:
                continue
            archive.write(path, relative)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def find_named(items: list[dict[str, Any]], field: str, name: str) -> dict[str, Any]:
    return next(
        (item for item in items if str(item.get(field) or "") == name),
        {},
    )


def verify_account(api: VolcOpenAPI) -> str:
    identity = api.call("sts", "GetCallerIdentity", "2018-01-01", {})
    account_id = str(identity.get("AccountId") or "")
    expected = os.getenv("MUA_ACCOUNT_ID", "").strip()
    if expected and account_id != expected:
        raise RuntimeError(
            f"refusing deployment: caller account {account_id} != expected account"
        )
    return account_id


def ensure_function(
    api: VolcOpenAPI,
    *,
    name: str,
    source_dir: Path,
    project: str,
) -> str:
    functions = api.call(
        "vefaas",
        "ListFunctions",
        "2021-03-03",
        {"PageNumber": 1, "PageSize": 100},
    ).get("Items") or []
    existing = find_named(functions, "Name", name)
    source = package_directory(source_dir)

    common = {
        "Command": "python3 static_server.py",
        "Port": 8000,
        "MemoryMB": 1024,
        "RequestTimeout": 120,
        "SourceType": "zip",
        "Source": source,
        "ProjectName": project,
    }
    if existing:
        function_id = str(existing.get("Id") or "")
        api.call(
            "vefaas",
            "UpdateFunction",
            "2021-03-03",
            {"Id": function_id, **common},
        )
    else:
        created = api.call(
            "vefaas",
            "CreateFunction",
            "2021-03-03",
            {
                "Name": name,
                "Runtime": "native-python3.12/v1",
                "Description": "Work Life Agent static frontend",
                **common,
            },
        )
        function_id = str(created.get("Id") or "")

    if not function_id:
        raise RuntimeError("CreateFunction/UpdateFunction returned no function ID")
    return function_id


def release_function(api: VolcOpenAPI, function_id: str) -> int:
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
        if state == "error" or "fail" in state:
            message = status.get("StatusMessage") or status.get("ErrorCode") or state
            raise RuntimeError(f"veFaaS release failed: {message}")
        time.sleep(5)
    raise TimeoutError("timed out waiting for veFaaS release")


def ensure_gateway(
    api: VolcOpenAPI,
    *,
    name: str,
    region: str,
    project: str,
) -> str:
    gateways = api.call(
        "apig",
        "ListGateways",
        "2021-03-03",
        {"PageNumber": 1, "PageSize": 100, "ProjectName": project},
    ).get("Items") or []
    existing = find_named(gateways, "Name", name)
    if existing:
        gateway_id = str(existing.get("Id") or "")
    else:
        vpcs = api.call(
            "vpc",
            "DescribeVpcs",
            "2020-04-01",
            {"PageNumber": 1, "PageSize": 100},
            method="GET",
        ).get("Vpcs") or []
        vpc = next((item for item in vpcs if item.get("IsDefault")), None)
        if not vpc:
            vpc = next(
                (item for item in vpcs if item.get("Status") == "Available"),
                None,
            )
        if not vpc:
            raise RuntimeError("no available VPC found for APIG")

        subnets = api.call(
            "vpc",
            "DescribeSubnets",
            "2020-04-01",
            {
                "VpcId": vpc["VpcId"],
                "PageNumber": 1,
                "PageSize": 100,
            },
            method="GET",
        ).get("Subnets") or []
        available = [
            item for item in subnets if item.get("Status") in {"", "Available"}
        ]
        if not available:
            raise RuntimeError(f"no available subnet found in VPC {vpc['VpcId']}")

        created = api.call(
            "apig",
            "CreateGateway",
            "2021-03-03",
            {
                "Name": name,
                "Region": region,
                "Type": "standard",
                "ProjectName": project,
                "NetworkSpec": {
                    "VpcId": vpc["VpcId"],
                    "SubnetIds": [item["SubnetId"] for item in available[:2]],
                },
            },
        )
        gateway_id = str(created.get("Id") or "")

    if not gateway_id:
        raise RuntimeError("APIG returned no gateway ID")

    for _ in range(45):
        gateway = api.call(
            "apig",
            "GetGateway",
            "2021-03-03",
            {"Id": gateway_id},
        ).get("Gateway") or {}
        status = str(gateway.get("Status") or "")
        if status == "Running":
            return gateway_id
        if status in {"Failed", "Error"}:
            raise RuntimeError(f"APIG gateway provisioning failed: {status}")
        time.sleep(8)
    raise TimeoutError("timed out waiting for APIG gateway")


def list_apig(
    api: VolcOpenAPI, action: str, version: str, body: dict[str, Any]
) -> list[dict[str, Any]]:
    return api.call("apig", action, version, body).get("Items") or []


def ensure_route(
    api: VolcOpenAPI,
    *,
    gateway_id: str,
    function_id: str,
    service_name: str,
    region: str,
) -> tuple[str, str, str, str]:
    upstream_name = f"{service_name}-upstream"
    upstream = find_named(
        list_apig(
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
        list_apig(
            api,
            "ListGatewayServices",
            "2021-03-03",
            {"GatewayId": gateway_id, "PageNumber": 1, "PageSize": 100},
        ),
        "Name",
        service_name,
    )
    if not service:
        service = api.call(
            "apig",
            "CreateGatewayService",
            "2021-03-03",
            {
                "GatewayId": gateway_id,
                "ServiceName": service_name,
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

    route_name = f"{service_name}-route"
    route = find_named(
        list_apig(
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
        raise RuntimeError("APIG did not return all required resource IDs")
    url = f"https://{service_id}.apigateway-{region}.volceapi.com/"
    return upstream_id, service_id, route_id, url


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--state-file", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--region", default="cn-beijing")
    parser.add_argument("--project", default="default")
    parser.add_argument("--function-name", default="work-life-agent-frontend")
    parser.add_argument("--gateway-name", default="work-life-agent-gateway")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_dotenv(args.env_file)
    api = VolcOpenAPI(args.region)
    account_id = verify_account(api)

    print("Creating or updating veFaaS function...")
    function_id = ensure_function(
        api,
        name=args.function_name,
        source_dir=args.source,
        project=args.project,
    )
    revision = release_function(api, function_id)
    state: dict[str, Any] = {
        "account_id": account_id,
        "region": args.region,
        "function_id": function_id,
        "revision": revision,
        "gateway_id": "",
        "upstream_id": "",
        "service_id": "",
        "route_id": "",
        "access_url": "",
    }
    save_state(args.state_file, state)

    print("Creating or reusing APIG gateway...")
    gateway_id = ensure_gateway(
        api,
        name=args.gateway_name,
        region=args.region,
        project=args.project,
    )
    upstream_id, service_id, route_id, access_url = ensure_route(
        api,
        gateway_id=gateway_id,
        function_id=function_id,
        service_name=args.function_name,
        region=args.region,
    )
    state.update(
        {
            "gateway_id": gateway_id,
            "upstream_id": upstream_id,
            "service_id": service_id,
            "route_id": route_id,
            "access_url": access_url,
        }
    )
    save_state(args.state_file, state)
    print(f"Frontend URL: {access_url}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Deployment failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
