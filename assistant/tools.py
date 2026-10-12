"""Tools for the work/life organizer agent."""

from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from google.adk.tools.tool_context import ToolContext

from .cloud_phone import client as mua_client
from .user_registry import (
    allocate_user_binding,
    assert_run_owner,
    rebind_user_to_pod,
    resolve_user_binding,
    save_run_owner,
    save_user_binding,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data"
STORE_PATH = Path(os.getenv("WORK_LIFE_STORE", DATA_DIR / "work_log.json"))

# Cloud-phone product/pod ids come from user bindings, configured pool entries,
# or Pods discovered under MUA_PRODUCT_ID. No tenant-specific ids are hardcoded.


def _today() -> str:
    return date.today().isoformat()


def _now_time() -> str:
    return datetime.now().strftime("%H:%M")


def _ensure_store() -> dict[str, Any]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STORE_PATH.exists():
        return {"todos": [], "meals": [], "sleep": []}
    try:
        data = json.loads(STORE_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        backup = STORE_PATH.with_suffix(f".broken-{datetime.now().strftime('%Y%m%d%H%M%S')}.json")
        STORE_PATH.rename(backup)
        return {"todos": [], "meals": [], "sleep": [], "warnings": [f"old store backed up to {backup.name}"]}
    for key in ("todos", "meals", "sleep"):
        data.setdefault(key, [])
    return data


def _save_store(data: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(STORE_PATH)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def add_todo(title: str, due_date: str = "", priority: str = "normal", notes: str = "") -> dict[str, Any]:
    """Add a personal todo item.

    Args:
        title: The task title.
        due_date: Optional due date in YYYY-MM-DD. Defaults to today.
        priority: Task priority, such as low, normal, high, or urgent.
        notes: Optional extra details.
    """
    data = _ensure_store()
    item = {
        "id": _new_id("todo"),
        "title": title.strip(),
        "due_date": (due_date or _today()).strip(),
        "priority": (priority or "normal").strip(),
        "notes": notes.strip(),
        "done": False,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "completed_at": "",
    }
    if not item["title"]:
        return {"ok": False, "error": "title is required"}
    data["todos"].append(item)
    _save_store(data)
    return {"ok": True, "todo": item}


def list_todos(target_date: str = "", include_done: bool = False) -> dict[str, Any]:
    """List personal todos for a date.

    Args:
        target_date: Date in YYYY-MM-DD. Defaults to today.
        include_done: Whether completed tasks should be included.
    """
    resolved_date = (target_date or _today()).strip()
    data = _ensure_store()
    todos = [
        item for item in data["todos"]
        if item.get("due_date") == resolved_date and (include_done or not item.get("done"))
    ]
    return {"ok": True, "date": resolved_date, "count": len(todos), "todos": todos}


def complete_todo(todo_id: str) -> dict[str, Any]:
    """Mark a personal todo item as completed.

    Args:
        todo_id: The todo id returned by add_todo or list_todos.
    """
    data = _ensure_store()
    for item in data["todos"]:
        if item.get("id") == todo_id:
            item["done"] = True
            item["completed_at"] = datetime.now().isoformat(timespec="seconds")
            _save_store(data)
            return {"ok": True, "todo": item}
    return {"ok": False, "error": f"todo not found: {todo_id}"}


def log_meal(meal_type: str, target_date: str = "", time: str = "", notes: str = "") -> dict[str, Any]:
    """Record a daily meal check-in.

    Args:
        meal_type: Meal type, such as breakfast, lunch, dinner, snack, or Chinese equivalents.
        target_date: Date in YYYY-MM-DD. Defaults to today.
        time: Meal time in HH:MM. Defaults to current local time.
        notes: Optional notes about food, location, or status.
    """
    data = _ensure_store()
    item = {
        "id": _new_id("meal"),
        "date": (target_date or _today()).strip(),
        "time": (time or _now_time()).strip(),
        "meal_type": meal_type.strip(),
        "notes": notes.strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    if not item["meal_type"]:
        return {"ok": False, "error": "meal_type is required"}
    data["meals"].append(item)
    _save_store(data)
    return {"ok": True, "meal": item}


def log_sleep(sleep_date: str = "", bedtime: str = "", wake_time: str = "", quality: str = "", notes: str = "") -> dict[str, Any]:
    """Record sleep for a date.

    Args:
        sleep_date: Sleep date in YYYY-MM-DD. Defaults to today.
        bedtime: Optional bedtime in HH:MM.
        wake_time: Optional wake time in HH:MM.
        quality: Optional quality label, such as good, normal, poor.
        notes: Optional notes.
    """
    data = _ensure_store()
    item = {
        "id": _new_id("sleep"),
        "date": (sleep_date or _today()).strip(),
        "bedtime": bedtime.strip(),
        "wake_time": wake_time.strip(),
        "quality": quality.strip(),
        "notes": notes.strip(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    data["sleep"].append(item)
    _save_store(data)
    return {"ok": True, "sleep": item}


def get_daily_summary(target_date: str = "") -> dict[str, Any]:
    """Get a structured daily summary of todos, meal check-ins, and sleep records.

    Args:
        target_date: Date in YYYY-MM-DD. Defaults to today.
    """
    resolved_date = (target_date or _today()).strip()
    data = _ensure_store()
    todos = [item for item in data["todos"] if item.get("due_date") == resolved_date]
    meals = [item for item in data["meals"] if item.get("date") == resolved_date]
    sleep = [item for item in data["sleep"] if item.get("date") == resolved_date]
    return {
        "ok": True,
        "date": resolved_date,
        "todos": todos,
        "meal_checkins": meals,
        "sleep_records": sleep,
        "stats": {
            "todo_total": len(todos),
            "todo_done": sum(1 for item in todos if item.get("done")),
            "meal_checkins": len(meals),
            "sleep_records": len(sleep),
        },
    }


def _ensure_volc_env_aliases() -> None:
    if not os.getenv("VOLC_ACCESSKEY") and os.getenv("VOLCENGINE_ACCESS_KEY"):
        os.environ["VOLC_ACCESSKEY"] = os.environ["VOLCENGINE_ACCESS_KEY"]
    if not os.getenv("VOLC_SECRETKEY") and os.getenv("VOLCENGINE_SECRET_KEY"):
        os.environ["VOLC_SECRETKEY"] = os.environ["VOLCENGINE_SECRET_KEY"]


def inspect_cloud_phone_run(run_id: str) -> dict[str, Any]:
    """Inspect an existing MUA cloud-phone run.

    Args:
        run_id: MUA run id.
    """
    try:
        _ensure_volc_env_aliases()
        return {
            "ok": True,
            "run_id": run_id,
            "steps": mua_client.get_mua_task_steps(run_id=run_id),
            "result": mua_client.get_mua_task_result(run_id=run_id),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def cancel_cloud_phone_run(run_id: str) -> dict[str, Any]:
    """Cancel an existing MUA cloud-phone run.

    Args:
        run_id: MUA run id.
    """
    try:
        _ensure_volc_env_aliases()
        return mua_client.cancel_mua_task(run_id=run_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def provision_user_cloud_phone(
    user_id: str,
    pod_name: str = "",
    configuration_code: str = "",
    dc: str = "",
    data_size: str = "",
) -> dict[str, Any]:
    """Provision and bind one new cloud-phone pod for a user.

    This is an explicit, potentially billable operation. It reuses an existing
    active binding and never creates a second pod for the same user.
    """
    resolved_user_id = (user_id or "").strip()
    if not resolved_user_id:
        return {"ok": False, "error": "user_id is required"}
    try:
        try:
            existing = resolve_user_binding(resolved_user_id)
            return {
                "ok": True,
                "action": "reuse_user_cloud_phone",
                "binding": existing,
                "created": False,
            }
        except (KeyError, PermissionError):
            pass

        _ensure_volc_env_aliases()
        created = mua_client.create_mua_pod(
            product_id=os.getenv("MUA_PRODUCT_ID", ""),
            pod_name=pod_name or f"work-life-{resolved_user_id[:24]}",
            configuration_code=configuration_code or os.getenv("MUA_CONFIGURATION_CODE", ""),
            dc=dc or os.getenv("MUA_CREATE_DC", ""),
            data_size=data_size or os.getenv("MUA_DATA_SIZE", ""),
            start=True,
        )
        binding = save_user_binding(
            resolved_user_id,
            product_id=str(created["product_id"]),
            pod_id=str(created["pod_id"]),
        )
        return {
            "ok": True,
            "action": "provision_user_cloud_phone",
            "created": True,
            "binding": binding,
            "provision": created,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _create_overflow_cloud_phone() -> dict[str, str]:
    _ensure_volc_env_aliases()
    result = mua_client.create_mua_pod(
        product_id=os.getenv("MUA_PRODUCT_ID", ""),
        pod_name=f"work-life-auto-{uuid.uuid4().hex[:12]}",
        configuration_code=os.getenv("MUA_CONFIGURATION_CODE", ""),
        dc=os.getenv("MUA_CREATE_DC", ""),
        data_size=os.getenv("MUA_DATA_SIZE", "4Gi"),
        start=True,
    )
    return {
        "product_id": str(result["product_id"]),
        "pod_id": str(result["pod_id"]),
    }


def _discover_existing_cloud_phones() -> list[dict[str, str]]:
    """List reusable Pods that already exist under the configured product."""
    _ensure_volc_env_aliases()
    product_id = os.getenv("MUA_PRODUCT_ID", "").strip()
    result = mua_client.list_mua_pods(product_id=product_id)
    pods = result.get("pods")
    if not isinstance(pods, list):
        raise RuntimeError("ListPod returned an invalid Pod list")

    discovered: list[dict[str, str]] = []
    for item in pods:
        if not isinstance(item, dict):
            continue
        pod_id = str(item.get("PodId") or item.get("pod_id") or "").strip()
        item_product_id = str(
            item.get("ProductId") or item.get("product_id") or product_id
        ).strip()
        archive_status = item.get("ArchiveStatus")
        if pod_id and item_product_id and archive_status in {None, 0, "0"}:
            discovered.append(
                {
                    "product_id": item_product_id,
                    "pod_id": pod_id,
                    "status": "ready",
                }
            )
    return discovered


def ensure_user_cloud_phone(user_id: str) -> dict[str, str]:
    """Reuse a binding or existing Pod before creating and binding a new one."""
    return allocate_user_binding(
        user_id,
        discoverer=_discover_existing_cloud_phones,
        provisioner=_create_overflow_cloud_phone,
    )


def get_cloud_phone_stream_session(user_id: str, ttl_seconds: int = 600) -> dict[str, Any]:
    """Create a short-lived stream session for the cloud phone bound to a user.

    Args:
        user_id: Stable frontend user id.
        ttl_seconds: STS token lifetime in seconds.
    """
    try:
        _ensure_volc_env_aliases()
        binding = ensure_user_cloud_phone(user_id)
        stream = mua_client.get_mua_stream_session(
            product_id=binding["product_id"],
            pod_id=binding["pod_id"],
            ttl_seconds=ttl_seconds,
            user_id=binding["user_id"],
        )
        stream["binding"] = binding
        return stream
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def start_bound_cloud_phone_task(
    user_id: str,
    prompt: str,
    wait: bool = False,
    max_step: int = 100,
    timeout: int = 600,
) -> dict[str, Any]:
    """Start a MUA task on the cloud phone bound to a frontend user."""
    if not prompt.strip():
        return {"ok": False, "error": "prompt is required"}
    try:
        _ensure_volc_env_aliases()
        binding = ensure_user_cloud_phone(user_id)
        started = mua_client.start_mua_task(
            prompt=prompt,
            product_id=binding["product_id"],
            pod_id=binding["pod_id"],
            max_step=max_step,
            timeout=timeout,
            retry_limit=2,
        )
        if started.get("run_id"):
            save_run_owner(str(started["run_id"]), user_id)
        started["binding"] = binding
        if wait and started.get("run_id"):
            waited = mua_client.wait_mua_task(
                run_id=started["run_id"],
                poll_interval=5,
                wait_timeout=timeout,
            )
            waited["binding"] = binding
            return waited
        return started
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def operate_my_cloud_phone(
    prompt: str,
    tool_context: ToolContext,
    wait: bool = False,
    max_step: int = 100,
    timeout: int = 600,
) -> dict[str, Any]:
    """Operate only the cloud phone bound to the current authenticated user."""
    return start_bound_cloud_phone_task(
        user_id=tool_context.user_id,
        prompt=prompt,
        wait=wait,
        max_step=max_step,
        timeout=timeout,
    )


def inspect_my_cloud_phone_run(
    run_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Inspect one cloud-phone run owned by the current authenticated user."""
    try:
        assert_run_owner(run_id, tool_context.user_id)
        return inspect_cloud_phone_run(run_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def cancel_my_cloud_phone_run(
    run_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Cancel one cloud-phone run owned by the current authenticated user."""
    try:
        assert_run_owner(run_id, tool_context.user_id)
        return cancel_cloud_phone_run(run_id)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def get_my_cloud_phone(tool_context: ToolContext) -> dict[str, Any]:
    """Return only the cloud phone bound to the current authenticated user."""
    try:
        return {"ok": True, "binding": ensure_user_cloud_phone(tool_context.user_id)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def switch_my_cloud_phone(
    pod_id: str,
    tool_context: ToolContext,
) -> dict[str, Any]:
    """Switch the current user to a requested existing cloud-phone Pod.

    Args:
        pod_id: Exact target Pod ID explicitly provided by the current user.
    """
    try:
        _ensure_volc_env_aliases()
        result = rebind_user_to_pod(
            tool_context.user_id,
            pod_id,
            discoverer=_discover_existing_cloud_phones,
        )
        return {
            "ok": True,
            "action": "switch_my_cloud_phone",
            **result,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
