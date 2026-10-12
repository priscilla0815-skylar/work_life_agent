#!/usr/bin/env python3
"""Volcengine MUA, ACEP, and stream-token client functions."""

from __future__ import annotations

import json
import os
import secrets
import time
import uuid
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path | None = None) -> None:
    target = path or (PROJECT_ROOT / ".env")
    if not target.exists():
        return
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip()


load_dotenv()


def require_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"missing environment variable: {name}")
    return value


def _sdk_core() -> Any:
    try:
        import volcenginesdkcore
    except ImportError as exc:  # pragma: no cover - import error path
        raise RuntimeError(
            "missing dependency volcenginesdkcore; install with: pip install -r requirements.txt"
        ) from exc
    return volcenginesdkcore


def _sdk_sts() -> Any:
    try:
        import volcenginesdksts
    except ImportError as exc:  # pragma: no cover - import error path
        raise RuntimeError(
            "missing dependency volcenginesdksts; install with: pip install -r requirements.txt"
        ) from exc
    return volcenginesdksts


def extract_results(step_resp: Any) -> list[dict[str, Any]]:
    if not isinstance(step_resp, dict):
        return []
    results = step_resp.get("Results")
    if results is None:
        inner = step_resp.get("Result")
        if isinstance(inner, dict):
            results = inner.get("Results")
    if not isinstance(results, list):
        return []
    return [item for item in results if isinstance(item, dict)]


class PortableMobileUseClient:
    service = "ipaas"
    version = "2023-08-01"

    def __init__(self, *, ak: str, sk: str, region: str = "cn-north-1") -> None:
        volcenginesdkcore = _sdk_core()
        configuration = volcenginesdkcore.Configuration()
        configuration.ak = ak
        configuration.sk = sk
        configuration.region = region
        configuration.auto_retry = False
        self.api = volcenginesdkcore.UniversalApi(volcenginesdkcore.ApiClient(configuration))
        self.region = region

    def _do_call(self, *, method: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        volcenginesdkcore = _sdk_core()
        return self.api.do_call(
            volcenginesdkcore.UniversalInfo(
                method=method,
                action=action,
                service=self.service,
                version=self.version,
                content_type="application/json",
            ),
            volcenginesdkcore.Flatten(body).flat(),
        )

    def run_agent_task_one_step(
        self,
        *,
        run_name: str,
        product_id: str,
        pod_id: str,
        user_prompt: str,
        max_step: int = 100,
        timeout: int = 300,
        system_prompt: str | None = None,
        retry_limit: int = 3,
        is_screen_record: bool = False,
        use_base64_screenshot: bool = False,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "RunName": run_name,
            "ProductId": product_id,
            "PodId": pod_id,
            "UserPrompt": user_prompt,
            "MaxStep": max_step,
            "Timeout": timeout,
            "RetryLimit": retry_limit,
            "IsScreenRecord": is_screen_record,
            "UseBase64Screenshot": use_base64_screenshot,
        }
        if system_prompt:
            body["SystemPrompt"] = system_prompt
        return self._do_call(method="POST", action="RunAgentTaskOneStep", body=body)

    def list_agent_run_current_step(self, run_id: str) -> dict[str, Any]:
        return self._do_call(
            method="GET",
            action="ListAgentRunCurrentStep",
            body={"RunId": run_id},
        )

    def get_agent_result(self, run_id: str) -> dict[str, Any]:
        return self._do_call(
            method="GET",
            action="GetAgentResult",
            body={"RunId": run_id},
        )

    def cancel_task(self, run_id: str) -> dict[str, Any]:
        return self._do_call(
            method="POST",
            action="CancelTask",
            body={"RunId": run_id},
        )


def get_client() -> PortableMobileUseClient:
    return PortableMobileUseClient(
        ak=require_env("VOLC_ACCESSKEY"),
        sk=require_env("VOLC_SECRETKEY"),
    )


def get_default_product_id(product_id: str | None = None) -> str:
    return (product_id or os.getenv("MUA_PRODUCT_ID", "")).strip() or require_env("MUA_PRODUCT_ID")


def get_default_pod_id(pod_id: str | None = None) -> str:
    return (pod_id or os.getenv("MUA_POD_ID", "")).strip() or require_env("MUA_POD_ID")


def get_default_pod_ids(pod_ids: list[str] | None = None) -> list[str]:
    normalized = [item.strip() for item in (pod_ids or []) if item and item.strip()]
    if normalized:
        return normalized
    return [get_default_pod_id()]


def _new_run_name(prefix: str = "portable-mua") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def start_mua_task(
    *,
    prompt: str,
    product_id: str | None = None,
    pod_id: str | None = None,
    run_name: str | None = None,
    max_step: int = 100,
    timeout: int = 300,
    retry_limit: int = 3,
    system_prompt: str | None = None,
    record: bool = False,
    base64_screenshot: bool = False,
) -> dict[str, Any]:
    client = get_client()
    resolved_product_id = get_default_product_id(product_id)
    resolved_pod_id = get_default_pod_id(pod_id)
    response = client.run_agent_task_one_step(
        run_name=run_name or _new_run_name(),
        product_id=resolved_product_id,
        pod_id=resolved_pod_id,
        user_prompt=prompt,
        max_step=max_step,
        timeout=timeout,
        system_prompt=system_prompt,
        retry_limit=retry_limit,
        is_screen_record=record,
        use_base64_screenshot=base64_screenshot,
    )
    return {
        "ok": True,
        "action": "start_mua_task",
        "run_id": response.get("RunId") or response.get("Result", {}).get("RunId"),
        "request": {
            "prompt": prompt,
            "product_id": resolved_product_id,
            "pod_id": resolved_pod_id,
            "run_name": run_name,
            "max_step": max_step,
            "timeout": timeout,
            "retry_limit": retry_limit,
            "record": record,
            "base64_screenshot": base64_screenshot,
        },
        "response": response,
    }


def get_mua_task_steps(*, run_id: str) -> dict[str, Any]:
    response = get_client().list_agent_run_current_step(run_id)
    steps = extract_results(response)
    return {
        "ok": True,
        "action": "get_mua_task_steps",
        "run_id": run_id,
        "step_count": len(steps),
        "steps": steps,
        "response": response,
    }


def get_mua_task_result(*, run_id: str) -> dict[str, Any]:
    response = get_client().get_agent_result(run_id)
    inner = response.get("Result")
    result_data = inner if isinstance(inner, dict) else response
    return {
        "ok": True,
        "action": "get_mua_task_result",
        "run_id": run_id,
        "is_success": result_data.get("IsSuccess"),
        "result": result_data,
        "response": response,
    }


def wait_mua_task(*, run_id: str, poll_interval: int = 5, wait_timeout: int = 900) -> dict[str, Any]:
    deadline = time.time() + wait_timeout
    latest_steps: list[dict[str, Any]] = []
    client = get_client()

    while True:
        if time.time() > deadline:
            return {
                "ok": False,
                "action": "wait_mua_task",
                "run_id": run_id,
                "error": f"wait timeout after {wait_timeout}s",
                "steps": latest_steps,
            }

        steps_resp = client.list_agent_run_current_step(run_id)
        latest_steps = extract_results(steps_resp)

        result_resp = client.get_agent_result(run_id)
        inner = result_resp.get("Result")
        result_data = inner if isinstance(inner, dict) else result_resp
        is_success = result_data.get("IsSuccess")
        if is_success is not None:
            return {
                "ok": True,
                "action": "wait_mua_task",
                "run_id": run_id,
                "is_success": is_success,
                "steps": latest_steps,
                "result": result_data,
                "response": result_resp,
            }
        time.sleep(poll_interval)


def cancel_mua_task(*, run_id: str) -> dict[str, Any]:
    response = get_client().cancel_task(run_id)
    return {
        "ok": True,
        "action": "cancel_mua_task",
        "run_id": run_id,
        "response": response,
    }


def get_universal_api() -> Any:
    volcenginesdkcore = _sdk_core()
    configuration = volcenginesdkcore.Configuration()
    configuration.ak = require_env("VOLC_ACCESSKEY")
    configuration.sk = require_env("VOLC_SECRETKEY")
    configuration.region = "cn-north-1"
    configuration.auto_retry = False
    return volcenginesdkcore.UniversalApi(volcenginesdkcore.ApiClient(configuration))


def acep_call(*, action: str, version: str, method: str, body: dict[str, Any]) -> dict[str, Any]:
    volcenginesdkcore = _sdk_core()
    return get_universal_api().do_call(
        volcenginesdkcore.UniversalInfo(
            method=method,
            action=action,
            service="ACEP",
            version=version,
            content_type="application/json",
        ),
        volcenginesdkcore.Flatten(body).flat(),
    )


def get_acep_task_info(*, task_id: str, product_id: str | None = None) -> dict[str, Any]:
    response = acep_call(
        action="GetTaskInfo",
        version="2025-05-01",
        method="GET",
        body={"ProductId": get_default_product_id(product_id), "TaskId": task_id},
    )
    inner = response.get("Result")
    result = inner if isinstance(inner, dict) else response
    return {
        "ok": True,
        "action": "get_acep_task_info",
        "task_id": task_id,
        "task": result,
        "response": response,
    }


def wait_acep_task(
    *,
    task_id: str,
    product_id: str | None = None,
    poll_interval: int = 5,
    wait_timeout: int = 900,
) -> dict[str, Any]:
    deadline = time.time() + wait_timeout
    latest: dict[str, Any] | None = None
    while True:
        if time.time() > deadline:
            return {
                "ok": False,
                "action": "wait_acep_task",
                "task_id": task_id,
                "error": f"wait timeout after {wait_timeout}s",
                "latest": latest,
            }
        latest = get_acep_task_info(task_id=task_id, product_id=product_id)
        task = latest.get("task")
        jobs = task.get("Jobs") if isinstance(task, dict) else None
        statuses = [
            item.get("Status")
            for item in jobs
            if isinstance(item, dict) and isinstance(item.get("Status"), int)
        ] if isinstance(jobs, list) else []
        if statuses and all(status in {100, -1, -2} for status in statuses):
            return {
                "ok": True,
                "action": "wait_acep_task",
                "task_id": task_id,
                "statuses": statuses,
                "task": task,
            }
        time.sleep(poll_interval)


def list_mua_pods(*, product_id: str | None = None, max_results: int = 100) -> dict[str, Any]:
    response = acep_call(
        action="ListPod",
        version="2025-05-01",
        method="POST",
        body={"ProductId": get_default_product_id(product_id), "MaxResults": max_results},
    )
    pods = response.get("Row") or response.get("Items") or response.get("Pods") or response.get("Result") or []
    return {
        "ok": True,
        "action": "list_mua_pods",
        "count": len(pods) if isinstance(pods, list) else None,
        "pods": pods,
        "response": response,
    }


def _select_mua_pod_resource(
    *,
    product_id: str,
    configuration_code: str = "",
    dc: str = "",
) -> dict[str, str]:
    response = acep_call(
        action="ListPodResource",
        version="2023-10-30",
        method="GET",
        body={"ProductId": product_id, "Offset": 0, "Count": 100},
    )
    inner = response.get("Result")
    result = inner if isinstance(inner, dict) else response
    rows = result.get("Row") if isinstance(result, dict) else None
    candidates: list[tuple[int, dict[str, Any]]] = []
    for item in rows if isinstance(rows, list) else []:
        if not isinstance(item, dict):
            continue
        item_configuration = str(item.get("ConfigurationCode") or "").strip()
        item_dc = str(item.get("Dc") or "").strip()
        if not item_configuration or not item_dc:
            continue
        if configuration_code and item_configuration != configuration_code:
            continue
        if dc and item_dc != dc:
            continue
        try:
            remaining = int(item.get("ApplyNum") or 0) - int(item.get("TotalNum") or 0)
        except (TypeError, ValueError):
            remaining = 0
        if remaining > 0:
            candidates.append((remaining, item))

    if not candidates:
        raise RuntimeError(
            "no available purchased cloud-phone resource; check ProductId or "
            "set MUA_CONFIGURATION_CODE and MUA_CREATE_DC explicitly"
        )

    _, selected = max(candidates, key=lambda entry: entry[0])
    return {
        "configuration_code": str(selected["ConfigurationCode"]),
        "dc": str(selected["Dc"]),
    }


def create_mua_pod(
    *,
    product_id: str | None = None,
    pod_name: str | None = None,
    configuration_code: str | None = None,
    dc: str | None = None,
    data_size: str | None = None,
    display_layout_id: str | None = None,
    start: bool = True,
) -> dict[str, Any]:
    """Create one cloud-phone pod through ACEP CreatePod.

    The cloud-phone business must already have purchased available resources.
    """
    resolved_product_id = get_default_product_id(product_id)
    resolved_configuration = (
        configuration_code or os.getenv("MUA_CONFIGURATION_CODE", "")
    ).strip()
    resolved_dc = (dc or os.getenv("MUA_CREATE_DC", "")).strip()
    if not resolved_configuration or not resolved_dc:
        selected = _select_mua_pod_resource(
            product_id=resolved_product_id,
            configuration_code=resolved_configuration,
            dc=resolved_dc,
        )
        resolved_configuration = resolved_configuration or selected["configuration_code"]
        resolved_dc = resolved_dc or selected["dc"]

    body: dict[str, Any] = {
        "ProductId": resolved_product_id,
        "ConfigurationCode": resolved_configuration,
        "Dc": resolved_dc,
        "DisplayLayoutId": (
            display_layout_id or os.getenv(
                "MUA_DISPLAY_LAYOUT_ID", "single-display-portrait"
            )
        ),
        "Start": start,
    }
    resolved_name = (pod_name or "").strip()
    resolved_data_size = (data_size or os.getenv("MUA_DATA_SIZE", "")).strip()
    if resolved_name:
        body["PodName"] = resolved_name
    if resolved_data_size:
        body["DataSize"] = resolved_data_size

    response = acep_call(
        action="CreatePod",
        version="2025-05-01",
        method="POST",
        body=body,
    )
    inner = response.get("Result")
    result = inner if isinstance(inner, dict) else response
    pod_id = result.get("PodId") or result.get("pod_id")
    pod_ids = result.get("PodIdList") or result.get("pod_id_list") or []
    if not pod_id and isinstance(pod_ids, list) and pod_ids:
        pod_id = pod_ids[0]
    if not pod_id:
        raise RuntimeError(f"CreatePod returned no PodId: {response}")
    return {
        "ok": True,
        "action": "create_mua_pod",
        "product_id": resolved_product_id,
        "pod_id": str(pod_id),
        "response": response,
    }


def preinstall_mua_app_from_url(
    *,
    download_url: str | None = None,
    absolute_path: str | None = None,
    product_id: str | None = None,
    pod_ids: list[str] | None = None,
    package_name: str | None = None,
    version_code: int | None = None,
    is_preinstall: bool = True,
) -> dict[str, Any]:
    resolved_download_url = (download_url or "").strip()
    resolved_absolute_path = (absolute_path or "").strip()
    if not resolved_download_url and not resolved_absolute_path:
        raise ValueError("download_url and absolute_path cannot both be empty")
    if resolved_download_url.lower().endswith(".xapk") and is_preinstall:
        raise ValueError("XAPK does not support IsPreinstall=true")

    body: dict[str, Any] = {
        "ProductId": get_default_product_id(product_id),
        "PodIdList": get_default_pod_ids(pod_ids),
        "IsPreinstall": is_preinstall,
    }
    if resolved_download_url:
        body["DownloadURL"] = resolved_download_url
    if resolved_absolute_path:
        body["AbsolutePath"] = resolved_absolute_path
    if package_name:
        body["PackageName"] = package_name
    if version_code is not None:
        body["VersionCode"] = version_code

    response = acep_call(action="AutoInstallApp", version="2023-10-30", method="POST", body=body)
    result = response.get("Result") if isinstance(response.get("Result"), dict) else response
    return {
        "ok": True,
        "action": "preinstall_mua_app_from_url",
        "task_id": result.get("TaskId") or result.get("task_id"),
        "response": response,
    }


def preinstall_mua_published_app(
    *,
    app_id: str,
    version_id: str,
    product_id: str | None = None,
    pod_ids: list[str] | None = None,
    install_type: int = 0,
    is_preinstall: bool = True,
) -> dict[str, Any]:
    response = acep_call(
        action="InstallApp",
        version="2023-10-30",
        method="POST",
        body={
            "AppId": app_id,
            "VersionId": version_id,
            "ProductId": get_default_product_id(product_id),
            "PodIdList": get_default_pod_ids(pod_ids),
            "InstallType": install_type,
            "IsPreinstall": is_preinstall,
        },
    )
    result = response.get("Result") if isinstance(response.get("Result"), dict) else response
    return {
        "ok": True,
        "action": "preinstall_mua_published_app",
        "task_id": result.get("TaskId") or result.get("task_id"),
        "response": response,
    }


def _stream_policy(account_id: str, product_id: str, pod_id: str, user_id: str) -> str:
    return json.dumps(
        {
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "ACEP:StartScreenShot",
                        "ACEP:MonitorPod",
                        "ACEP:PodStart",
                        "ACEP:BatchPodStart",
                        "ACEP:StartPodEventSync",
                        "ACEP:UpdatePodEventSync",
                        "ACEP:GetMediaToken",
                        "ACEP:ListPod",
                        "ACEP:GetTaskInfo",
                    ],
                    "Resource": [
                        f"trn:ACEP::{account_id}:product_id/{product_id}",
                        f"trn:ACEP::{account_id}:pod_id/{pod_id}",
                        f"trn:ACEP::{account_id}:user_id/{user_id}",
                    ],
                }
            ]
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def get_mua_stream_session(
    *,
    product_id: str | None = None,
    pod_id: str | None = None,
    account_id: str | None = None,
    role_trn: str | None = None,
    ttl_seconds: int | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    resolved_product_id = get_default_product_id(product_id)
    resolved_pod_id = get_default_pod_id(pod_id)
    resolved_account_id = (account_id or os.getenv("MUA_ACCOUNT_ID", "")).strip() or require_env("MUA_ACCOUNT_ID")
    resolved_ttl_seconds = ttl_seconds or int(os.getenv("MUA_STREAM_TOKEN_TTL_SECONDS", "600"))
    resolved_user_id = user_id or f"portable-{secrets.token_hex(6)}"
    token_mode = os.getenv("MUA_STREAM_TOKEN_MODE", "legacy_sts").strip().lower()

    if token_mode == "legacy_sts":
        response = acep_call(
            action="STSToken",
            version="2022-08-01",
            method="GET",
            body={
                "ak": require_env("VOLC_ACCESSKEY"),
                "sk": require_env("VOLC_SECRETKEY"),
                "expire": resolved_ttl_seconds,
            },
        )
        token = {
            "AccessKeyID": response.get("ak"),
            "SecretAccessKey": response.get("sk"),
            "SessionToken": response.get("token"),
            "CurrentTime": response.get("create_at"),
            "ExpiredTime": response.get("expire_at"),
        }
    elif token_mode == "assume_role":
        volcenginesdkcore = _sdk_core()
        volcenginesdksts = _sdk_sts()
        resolved_role_trn = (role_trn or os.getenv("MUA_STS_ROLE_TRN", "")).strip() or require_env("MUA_STS_ROLE_TRN")

        configuration = volcenginesdkcore.Configuration()
        configuration.ak = require_env("VOLC_ACCESSKEY")
        configuration.sk = require_env("VOLC_SECRETKEY")
        configuration.region = "cn-north-1"
        configuration.auto_retry = False

        api = volcenginesdksts.STSApi(volcenginesdkcore.ApiClient(configuration))
        response = api.assume_role(
            volcenginesdksts.AssumeRoleRequest(
                duration_seconds=resolved_ttl_seconds,
                policy=_stream_policy(resolved_account_id, resolved_product_id, resolved_pod_id, resolved_user_id),
                role_session_name=f"mua-stream-{resolved_user_id}"[:64],
                role_trn=resolved_role_trn,
            )
        )
        credentials = getattr(response, "credentials", None)
        token = {
            "AccessKeyID": getattr(credentials, "access_key_id", None),
            "SecretAccessKey": getattr(credentials, "secret_access_key", None),
            "SessionToken": getattr(credentials, "session_token", None),
            "CurrentTime": getattr(credentials, "current_time", None),
            "ExpiredTime": getattr(credentials, "expired_time", None),
        }
    else:
        raise ValueError("MUA_STREAM_TOKEN_MODE must be legacy_sts or assume_role")

    if not all(isinstance(value, str) and value for value in token.values()):
        raise RuntimeError("stream token service returned incomplete credentials")

    return {
        "ok": True,
        "action": "get_mua_stream_session",
        "account_id": resolved_account_id,
        "product_id": resolved_product_id,
        "pod_id": resolved_pod_id,
        "user_id": resolved_user_id,
        "token_mode": token_mode,
        "token": token,
    }


__all__ = [
    "extract_results",
    "start_mua_task",
    "get_mua_task_steps",
    "get_mua_task_result",
    "wait_mua_task",
    "cancel_mua_task",
    "get_acep_task_info",
    "wait_acep_task",
    "list_mua_pods",
    "create_mua_pod",
    "preinstall_mua_app_from_url",
    "preinstall_mua_published_app",
    "get_mua_stream_session",
]
