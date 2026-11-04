import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from riyansh_bs_integration.core.errors import IntegrationError
from riyansh_bs_integration.core.logging import integration_endpoint


class TestAPI34TransactionSafety(unittest.TestCase):

    def setUp(self):
        self.db = SimpleNamespace(rollback=Mock())
        self.response = SimpleNamespace(http_status_code=200)

        self.frappe = types.ModuleType("frappe")
        self.frappe.db = self.db
        self.frappe.local = SimpleNamespace(response=self.response)
        self.frappe.log_error = Mock()

        self.modules = patch.dict(
            sys.modules,
            {"frappe": self.frappe},
        )
        self.modules.start()

        self.auth = patch(
            "riyansh_bs_integration.core.auth.require_integration_access"
        )
        self.auth.start()

        self.logging = patch(
            "riyansh_bs_integration.core.logging._write_api_log_safely"
        )
        self.log_mock = self.logging.start()

    def tearDown(self):
        self.logging.stop()
        self.auth.stop()
        self.modules.stop()

    def test_integration_error_triggers_rollback(self):
        @integration_endpoint("BS-03", reference_key="bs_order_id")
        def handler(payload=None, correlation_id=None):
            raise IntegrationError(
                "ITEM_NOT_FOUND",
                "Item does not exist",
                422,
                field="item_code",
            )

        result = handler(
            payload={"bs_order_id": "TEST-001"}
        )

        self.db.rollback.assert_called_once()
        self.assertFalse(result["success"])
        self.assertEqual(
            result["error"]["code"],
            "ITEM_NOT_FOUND",
        )
        self.assertEqual(
            self.response.http_status_code,
            422,
        )

    def test_unexpected_exception_triggers_rollback(self):
        @integration_endpoint("BS-04", reference_key="bs_credit_note_id")
        def handler(payload=None, correlation_id=None):
            raise RuntimeError("Simulated database failure")

        result = handler(
            payload={"bs_credit_note_id": "TEST-CN-001"}
        )

        self.db.rollback.assert_called_once()
        self.assertFalse(result["success"])
        self.assertEqual(
            result["error"]["code"],
            "INTERNAL_ERROR",
        )
        self.assertEqual(
            self.response.http_status_code,
            500,
        )

    def test_success_does_not_trigger_rollback(self):
        @integration_endpoint("BS-03", reference_key="bs_order_id")
        def handler(payload=None, correlation_id=None):
            return {
                "success": True,
                "data": {"sales_order": "TEST-SO-001"},
            }

        result = handler(
            payload={"bs_order_id": "TEST-001"}
        )

        self.db.rollback.assert_not_called()
        self.assertTrue(result["success"])


if __name__ == "__main__":
    unittest.main()
