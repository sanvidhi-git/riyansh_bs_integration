import sys
import unittest

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from riyansh_bs_integration.core import logging as api_logging


class TestAPI34LoggingReadiness(unittest.TestCase):

    def test_raw_json_request_is_logged_with_reference(self):
        payload = {
            "bs_order_id": "BS-ORDER-00001",
            "customer_id": "CUSTOMER-00001",
            "items": [],
        }

        fake_frappe = SimpleNamespace(
            request=SimpleNamespace(
                get_json=MagicMock(return_value=payload)
            ),
            local=SimpleNamespace(
                response=SimpleNamespace(http_status_code=201)
            ),
        )

        with patch.dict(sys.modules, {"frappe": fake_frappe}):
            result = api_logging._inbound_payload((), {})

        self.assertEqual(result["bs_order_id"], payload["bs_order_id"])

    def test_explicit_payload_takes_priority(self):
        fake_frappe = SimpleNamespace(
            request=SimpleNamespace(
                get_json=MagicMock(return_value={"bs_order_id": "RAW"})
            )
        )

        with patch.dict(sys.modules, {"frappe": fake_frappe}):
            result = api_logging._inbound_payload(
                (),
                {"payload": {"bs_order_id": "EXPLICIT"}},
            )

        self.assertEqual(result["bs_order_id"], "EXPLICIT")
        fake_frappe.request.get_json.assert_not_called()

    def test_success_status_is_201(self):
        fake_frappe = SimpleNamespace(
            local=SimpleNamespace(
                response=SimpleNamespace(http_status_code=201)
            )
        )

        with patch.dict(sys.modules, {"frappe": fake_frappe}):
            self.assertEqual(api_logging._response_http_status(), 201)

    def test_duplicate_status_is_200(self):
        fake_frappe = SimpleNamespace(
            local=SimpleNamespace(
                response=SimpleNamespace(http_status_code=200)
            )
        )

        with patch.dict(sys.modules, {"frappe": fake_frappe}):
            self.assertEqual(api_logging._response_http_status(), 200)

    def test_decorator_logs_raw_body_reference_and_status(self):
        payload = {
            "bs_order_id": "BS-ORDER-00001",
            "items": [],
        }

        fake_frappe = SimpleNamespace(
            request=SimpleNamespace(
                get_json=MagicMock(return_value=payload)
            ),
            local=SimpleNamespace(
                response=SimpleNamespace(http_status_code=200)
            ),
        )

        from riyansh_bs_integration.core import auth

        for status in (201, 200):
            fake_frappe.local.response.http_status_code = status

            with (
                patch.dict(sys.modules, {"frappe": fake_frappe}),
                patch.object(
                    auth,
                    "require_integration_access",
                    return_value=None,
                ),
                patch.object(
                    api_logging,
                    "_write_api_log_safely",
                ) as writer,
            ):

                @api_logging.integration_endpoint(
                    "BS-03", reference_key="bs_order_id"
                )
                def handler(correlation_id=None):
                    return {
                        "success": True,
                        "correlation_id": correlation_id,
                    }

                result = handler()

                self.assertTrue(result["success"])

                logged = writer.call_args.kwargs

                self.assertEqual(
                    logged["reference"], "BS-ORDER-00001"
                )
                self.assertEqual(
                    logged["request_body"]["bs_order_id"],
                    "BS-ORDER-00001",
                )
                self.assertEqual(logged["http_status"], status)
                self.assertEqual(logged["status"], "Success")


if __name__ == "__main__":
    unittest.main()
