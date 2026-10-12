"""User-to-cloud-phone binding helpers.

OIDC integration should eventually replace only user identity extraction. The
binding lookup stays the same: a stable user id resolves to one product/pod pair.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

import fcntl


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BINDINGS_PATH = PROJECT_ROOT / "config" / "user_pod_bindings.json"
DEFAULT_POOL_PATH = PROJECT_ROOT / "config" / "pod_pool.json"
DEFAULT_RUN_OWNERS_PATH = PROJECT_ROOT / "config" / "mua_run_owners.json"


def _bindings_path() -> Path:
    return Path(os.getenv("USER_POD_BINDINGS_PATH", DEFAULT_BINDINGS_PATH)).expanduser()


def _pool_path() -> Path:
    return Path(os.getenv("MUA_POD_POOL_PATH", DEFAULT_POOL_PATH)).expanduser()


def _run_owners_path() -> Path:
    return Path(os.getenv("MUA_RUN_OWNERS_PATH", DEFAULT_RUN_OWNERS_PATH)).expanduser()


def _load_persisted_bindings() -> dict[str, Any]:
    raw = os.getenv("USER_POD_BINDINGS_JSON", "").strip()
    if raw:
        loaded = json.loads(raw)
        return loaded if isinstance(loaded, dict) else {}
    return _load_file_bindings()


def _load_pod_pool() -> list[dict[str, str]]:
    path = _pool_path()
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("pods") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError(f"pod pool must contain a list: {path}")
    pool: list[dict[str, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        product_id = str(item.get("product_id") or item.get("ProductId") or "").strip()
        pod_id = str(item.get("pod_id") or item.get("PodId") or "").strip()
        status = str(item.get("status") or "available").strip().lower()
        if product_id and pod_id and status in {"available", "active", "ready"}:
            pool.append({"product_id": product_id, "pod_id": pod_id, "status": status})
    return pool


@contextmanager
def _bindings_lock():
    path = _bindings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _write_file_bindings(data: dict[str, Any]) -> None:
    path = _bindings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)


def _load_file_bindings() -> dict[str, Any]:
    path = _bindings_path()
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"user bindings file must contain a JSON object: {path}")
    return data


def load_user_bindings() -> dict[str, dict[str, str]]:
    """Load user bindings; every binding must come from the persisted store."""
    bindings: dict[str, dict[str, str]] = {}
    loaded = _load_persisted_bindings()

    for user_id, item in loaded.items():
        if not isinstance(item, dict):
            continue
        product_id = str(item.get("product_id") or item.get("ProductId") or "").strip()
        pod_id = str(item.get("pod_id") or item.get("PodId") or "").strip()
        if product_id and pod_id:
            bindings[str(user_id)] = {
                "product_id": product_id,
                "pod_id": pod_id,
                "status": str(item.get("status") or "active"),
            }
    return bindings


def allocate_user_binding(
    user_id: str,
    provisioner: Callable[[], dict[str, str]] | None = None,
    discoverer: Callable[[], list[dict[str, str]]] | None = None,
) -> dict[str, str]:
    """Atomically reuse, discover, allocate, or provision one Pod for a user."""
    resolved_user_id = (user_id or "").strip()
    if not resolved_user_id:
        raise ValueError("user_id is required")
    if os.getenv("USER_POD_BINDINGS_JSON", "").strip():
        raise RuntimeError("USER_POD_BINDINGS_JSON is read-only; use USER_POD_BINDINGS_PATH for allocation")

    with _bindings_lock():
        bindings = load_user_bindings()
        existing = bindings.get(resolved_user_id)
        if existing and existing.get("status") == "active":
            return {
                "user_id": resolved_user_id,
                "product_id": existing["product_id"],
                "pod_id": existing["pod_id"],
                "status": "active",
            }

        pool = _load_pod_pool()
        known_pod_ids = {item["pod_id"] for item in pool}
        if discoverer is not None:
            discovered = discoverer()
            if not isinstance(discovered, list):
                raise TypeError("cloud phone Pod discoverer must return a list")
            for item in discovered:
                if not isinstance(item, dict):
                    continue
                product_id = str(item.get("product_id") or item.get("ProductId") or "").strip()
                pod_id = str(item.get("pod_id") or item.get("PodId") or "").strip()
                if not product_id or not pod_id or pod_id in known_pod_ids:
                    continue
                pool.append(
                    {
                        "product_id": product_id,
                        "pod_id": pod_id,
                        "status": "ready",
                        "source": "ListPod",
                    }
                )
                known_pod_ids.add(pod_id)

        used_pods = {
            str(item.get("pod_id"))
            for item in bindings.values()
            if item.get("status") == "active"
        }
        candidate = next((item for item in pool if item["pod_id"] not in used_pods), None)
        if not candidate:
            if provisioner is None:
                raise RuntimeError(f"no available cloud phone Pod for user: {resolved_user_id}")
            created = provisioner()
            product_id = str(created.get("product_id") or "").strip()
            pod_id = str(created.get("pod_id") or "").strip()
            if not product_id or not pod_id:
                raise RuntimeError(f"CreatePod returned an invalid binding: {created}")
            persisted = _load_persisted_bindings()
            persisted[resolved_user_id] = {
                "product_id": product_id,
                "pod_id": pod_id,
                "status": "active",
                "allocated_at": str(int(time.time())),
                "source": "CreatePod",
            }
            _write_file_bindings(persisted)
            return {
                "user_id": resolved_user_id,
                "product_id": product_id,
                "pod_id": pod_id,
                "status": "active",
                "source": "CreatePod",
            }

        persisted = _load_persisted_bindings()
        persisted[resolved_user_id] = {
            "product_id": candidate["product_id"],
            "pod_id": candidate["pod_id"],
            "status": "active",
            "allocated_at": str(int(time.time())),
            "source": candidate.get("source", "PodPool"),
        }
        _write_file_bindings(persisted)
        return {
            "user_id": resolved_user_id,
            "product_id": candidate["product_id"],
            "pod_id": candidate["pod_id"],
            "status": "active",
            "source": candidate.get("source", "PodPool"),
        }


def rebind_user_to_pod(
    user_id: str,
    pod_id: str,
    *,
    discoverer: Callable[[], list[dict[str, str]]],
) -> dict[str, Any]:
    """Atomically switch a user to an existing, unbound Pod."""
    resolved_user_id = (user_id or "").strip()
    resolved_pod_id = (pod_id or "").strip()
    if not resolved_user_id:
        raise ValueError("user_id is required")
    if not resolved_pod_id:
        raise ValueError("pod_id is required")
    if os.getenv("USER_POD_BINDINGS_JSON", "").strip():
        raise RuntimeError(
            "USER_POD_BINDINGS_JSON is read-only; "
            "use USER_POD_BINDINGS_PATH for allocation"
        )

    with _bindings_lock():
        discovered = discoverer()
        if not isinstance(discovered, list):
            raise TypeError("cloud phone Pod discoverer must return a list")

        target: dict[str, str] | None = None
        for item in discovered:
            if not isinstance(item, dict):
                continue
            candidate_pod_id = str(
                item.get("pod_id") or item.get("PodId") or ""
            ).strip()
            product_id = str(
                item.get("product_id") or item.get("ProductId") or ""
            ).strip()
            if candidate_pod_id == resolved_pod_id and product_id:
                target = {"product_id": product_id, "pod_id": candidate_pod_id}
                break
        if target is None:
            raise ValueError(
                "requested Pod does not exist under the configured product"
            )

        bindings = load_user_bindings()
        for owner_user_id, binding in bindings.items():
            if (
                owner_user_id != resolved_user_id
                and binding.get("status") == "active"
                and binding.get("pod_id") == resolved_pod_id
            ):
                raise PermissionError("requested Pod is already bound")

        previous = bindings.get(resolved_user_id)
        if (
            previous
            and previous.get("status") == "active"
            and previous.get("pod_id") == resolved_pod_id
        ):
            return {
                "changed": False,
                "previous_binding": {
                    "product_id": previous["product_id"],
                    "pod_id": previous["pod_id"],
                    "status": "active",
                },
                "binding": {
                    "user_id": resolved_user_id,
                    "product_id": previous["product_id"],
                    "pod_id": previous["pod_id"],
                    "status": "active",
                },
            }

        persisted = _load_file_bindings()
        persisted[resolved_user_id] = {
            "product_id": target["product_id"],
            "pod_id": target["pod_id"],
            "status": "active",
            "allocated_at": str(int(time.time())),
            "source": "UserRequestedPodSwitch",
        }
        _write_file_bindings(persisted)
        return {
            "changed": True,
            "previous_binding": previous,
            "binding": {
                "user_id": resolved_user_id,
                "product_id": target["product_id"],
                "pod_id": target["pod_id"],
                "status": "active",
                "source": "UserRequestedPodSwitch",
            },
        }


def resolve_user_binding(user_id: str) -> dict[str, str]:
    """Resolve a user id to its cloud-phone binding."""
    resolved_user_id = (user_id or "").strip()
    if not resolved_user_id:
        raise ValueError("user_id is required")
    bindings = load_user_bindings()
    binding = bindings.get(resolved_user_id)
    if not binding:
        raise KeyError(f"user has no cloud-phone binding: {resolved_user_id}")
    if binding.get("status") != "active":
        raise PermissionError(f"user cloud-phone binding is not active: {resolved_user_id}")
    return {
        "user_id": resolved_user_id,
        "product_id": binding["product_id"],
        "pod_id": binding["pod_id"],
        "status": binding.get("status", "active"),
    }


def save_user_binding(
    user_id: str,
    *,
    product_id: str,
    pod_id: str,
    status: str = "active",
) -> dict[str, str]:
    """Persist a newly provisioned cloud-phone binding."""
    resolved_user_id = (user_id or "").strip()
    if not resolved_user_id:
        raise ValueError("user_id is required")
    if not product_id.strip() or not pod_id.strip():
        raise ValueError("product_id and pod_id are required")

    with _bindings_lock():
        data = _load_file_bindings()
        data[resolved_user_id] = {
            "product_id": product_id.strip(),
            "pod_id": pod_id.strip(),
            "status": status.strip() or "active",
        }
        _write_file_bindings(data)
    return {
        "user_id": resolved_user_id,
        "product_id": product_id.strip(),
        "pod_id": pod_id.strip(),
        "status": status.strip() or "active",
    }


def save_run_owner(run_id: str, user_id: str) -> None:
    """Persist ownership of an MUA run without exposing other users' runs."""
    resolved_run_id = (run_id or "").strip()
    resolved_user_id = (user_id or "").strip()
    if not resolved_run_id or not resolved_user_id:
        return

    path = _run_owners_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            data: dict[str, Any] = {}
            if path.exists():
                loaded = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    data = loaded
            data[resolved_run_id] = {
                "user_id": resolved_user_id,
                "created_at": str(int(time.time())),
            }
            _write_json_atomic(path, data)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def assert_run_owner(run_id: str, user_id: str) -> None:
    """Reject access unless the MUA run belongs to the authenticated user."""
    resolved_run_id = (run_id or "").strip()
    resolved_user_id = (user_id or "").strip()
    path = _run_owners_path()
    if not resolved_run_id or not resolved_user_id or not path.exists():
        raise PermissionError("cloud-phone run is not available for this user")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    owner = loaded.get(resolved_run_id) if isinstance(loaded, dict) else None
    if not isinstance(owner, dict) or owner.get("user_id") != resolved_user_id:
        raise PermissionError("cloud-phone run is not available for this user")


def _write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        delete=False,
    ) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temp_path = Path(handle.name)
    temp_path.replace(path)
