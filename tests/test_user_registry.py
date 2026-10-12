from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from assistant.user_registry import allocate_user_binding, rebind_user_to_pod


class AllocateUserBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.bindings_path = root / "bindings.json"
        self.pool_path = root / "pool.json"
        self.env = patch.dict(
            os.environ,
            {
                "USER_POD_BINDINGS_JSON": "",
                "USER_POD_BINDINGS_PATH": str(self.bindings_path),
                "MUA_POD_POOL_PATH": str(self.pool_path),
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.temp_dir.cleanup()

    def write_bindings(self, data: dict[str, object]) -> None:
        self.bindings_path.write_text(json.dumps(data), encoding="utf-8")

    def write_pool(self, pods: list[dict[str, str]]) -> None:
        self.pool_path.write_text(json.dumps({"pods": pods}), encoding="utf-8")

    def test_reuses_active_binding_without_discovery_or_creation(self) -> None:
        self.write_bindings(
            {
                "user-1": {
                    "product_id": "product-1",
                    "pod_id": "pod-1",
                    "status": "active",
                }
            }
        )
        discoverer = Mock()
        provisioner = Mock()

        result = allocate_user_binding(
            "user-1", discoverer=discoverer, provisioner=provisioner
        )

        self.assertEqual(result["pod_id"], "pod-1")
        discoverer.assert_not_called()
        provisioner.assert_not_called()

    def test_discovers_and_assigns_first_unbound_cloud_pod(self) -> None:
        self.write_bindings(
            {
                "user-1": {
                    "product_id": "product-1",
                    "pod_id": "pod-1",
                    "status": "active",
                }
            }
        )

        result = allocate_user_binding(
            "user-2",
            discoverer=lambda: [
                {"product_id": "product-1", "pod_id": "pod-1"},
                {"product_id": "product-1", "pod_id": "pod-2"},
            ],
            provisioner=Mock(),
        )

        self.assertEqual(result["pod_id"], "pod-2")
        self.assertEqual(result["source"], "ListPod")
        persisted = json.loads(self.bindings_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["user-2"]["pod_id"], "pod-2")

    def test_configured_pool_precedes_discovered_pods(self) -> None:
        self.write_pool(
            [
                {
                    "product_id": "product-1",
                    "pod_id": "configured-pod",
                    "status": "available",
                }
            ]
        )

        result = allocate_user_binding(
            "user-1",
            discoverer=lambda: [
                {"product_id": "product-1", "pod_id": "discovered-pod"}
            ],
            provisioner=Mock(),
        )

        self.assertEqual(result["pod_id"], "configured-pod")
        self.assertEqual(result["source"], "PodPool")

    def test_discovery_failure_does_not_create_a_pod(self) -> None:
        provisioner = Mock()

        with self.assertRaisesRegex(RuntimeError, "ListPod unavailable"):
            allocate_user_binding(
                "user-1",
                discoverer=Mock(side_effect=RuntimeError("ListPod unavailable")),
                provisioner=provisioner,
            )

        provisioner.assert_not_called()

    def test_creates_only_after_all_existing_pods_are_bound(self) -> None:
        self.write_bindings(
            {
                "user-1": {
                    "product_id": "product-1",
                    "pod_id": "pod-1",
                    "status": "active",
                }
            }
        )
        provisioner = Mock(
            return_value={"product_id": "product-1", "pod_id": "pod-new"}
        )

        result = allocate_user_binding(
            "user-2",
            discoverer=lambda: [{"product_id": "product-1", "pod_id": "pod-1"}],
            provisioner=provisioner,
        )

        self.assertEqual(result["pod_id"], "pod-new")
        self.assertEqual(result["source"], "CreatePod")
        provisioner.assert_called_once_with()

    def test_rebinds_user_to_requested_unbound_pod(self) -> None:
        self.write_bindings(
            {
                "user-1": {
                    "product_id": "product-1",
                    "pod_id": "pod-1",
                    "status": "active",
                }
            }
        )

        result = rebind_user_to_pod(
            "user-1",
            "pod-3",
            discoverer=lambda: [
                {"product_id": "product-1", "pod_id": "pod-1"},
                {"product_id": "product-1", "pod_id": "pod-3"},
            ],
        )

        self.assertTrue(result["changed"])
        self.assertEqual(result["previous_binding"]["pod_id"], "pod-1")
        self.assertEqual(result["binding"]["pod_id"], "pod-3")
        persisted = json.loads(self.bindings_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["user-1"]["pod_id"], "pod-3")
        self.assertEqual(
            persisted["user-1"]["source"], "UserRequestedPodSwitch"
        )

    def test_rebind_rejects_pod_bound_to_another_user(self) -> None:
        self.write_bindings(
            {
                "user-1": {
                    "product_id": "product-1",
                    "pod_id": "pod-1",
                    "status": "active",
                },
                "user-2": {
                    "product_id": "product-1",
                    "pod_id": "pod-2",
                    "status": "active",
                },
            }
        )

        with self.assertRaisesRegex(PermissionError, "already bound"):
            rebind_user_to_pod(
                "user-1",
                "pod-2",
                discoverer=lambda: [
                    {"product_id": "product-1", "pod_id": "pod-1"},
                    {"product_id": "product-1", "pod_id": "pod-2"},
                ],
            )

        persisted = json.loads(self.bindings_path.read_text(encoding="utf-8"))
        self.assertEqual(persisted["user-1"]["pod_id"], "pod-1")

    def test_rebind_rejects_unknown_pod(self) -> None:
        self.write_bindings(
            {
                "user-1": {
                    "product_id": "product-1",
                    "pod_id": "pod-1",
                    "status": "active",
                }
            }
        )

        with self.assertRaisesRegex(ValueError, "does not exist"):
            rebind_user_to_pod(
                "user-1",
                "unknown-pod",
                discoverer=lambda: [
                    {"product_id": "product-1", "pod_id": "pod-1"}
                ],
            )

    def test_rebind_to_current_pod_is_idempotent(self) -> None:
        self.write_bindings(
            {
                "user-1": {
                    "product_id": "product-1",
                    "pod_id": "pod-1",
                    "status": "active",
                }
            }
        )

        result = rebind_user_to_pod(
            "user-1",
            "pod-1",
            discoverer=lambda: [
                {"product_id": "product-1", "pod_id": "pod-1"}
            ],
        )

        self.assertFalse(result["changed"])
        self.assertEqual(result["binding"]["pod_id"], "pod-1")


if __name__ == "__main__":
    unittest.main()
