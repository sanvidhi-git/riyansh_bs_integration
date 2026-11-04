import unittest
from unittest.mock import Mock

from riyansh_bs_integration.core.errors import IntegrationError
from riyansh_bs_integration.services.order_service import (
    validate_order_payload,
    _get_or_create_shipping_address,
)
from riyansh_bs_integration.services.credit_note_service import (
    validate_credit_note_payload,
)


class TestAPI34MinimalContract(unittest.TestCase):

    def order_payload(self):
        return {
            "bs_order_id": "BS-ORDER-00001",
            "customer_id": "CUSTOMER-00001",
            "order_datetime": "2026-11-03T12:00:00+05:30",
            "warehouse_code": "SANGAMNER",
            "shipping_address": {
                "name": "Customer Name",
                "mobile": "9876543210",
                "address_line_1": "Address",
                "city": "Pune",
                "state": "Maharashtra",
                "pincode": "411001",
                "country": "India",
            },
            "items": [
                {
                    "item_code": "ITEM-CODE",
                    "qty": 1,
                    "uom": "Nos",
                    "rate": 1000,
                }
            ],
        }

    def credit_payload(self):
        return {
            "bs_credit_note_id": "BS-CN-00001",
            "bs_order_id": "BS-ORDER-00001",
            "customer_id": "CUSTOMER-00001",
            "credit_note_datetime": "2026-11-03T14:00:00+05:30",
            "warehouse_code": "SANGAMNER",
            "items": [
                {"item_code": "ITEM-CODE", "qty": 1, "rate": 1000}
            ],
        }

    def test_api3_exact_payload(self):
        payload = self.order_payload()
        result = validate_order_payload(payload)
        self.assertEqual(
            result["shipping_address"], payload["shipping_address"]
        )
        self.assertEqual(result["items"][0]["uom"], "Nos")
        self.assertEqual(result["currency"], "INR")
        self.assertNotIn("grand_total", result)

    def test_api3_requires_shipping_address(self):
        payload = self.order_payload()
        del payload["shipping_address"]
        with self.assertRaises(IntegrationError):
            validate_order_payload(payload)

    def test_api3_derives_uom_when_missing(self):
        payload = self.order_payload()
        del payload["items"][0]["uom"]
        result = validate_order_payload(payload)
        self.assertIsNone(result["items"][0]["uom"])

    def test_api4_default_reason(self):
        result = validate_credit_note_payload(
            self.credit_payload()
        )
        self.assertEqual(
            result["reason"], "Customer return approved"
        )
        self.assertEqual(result["status"], "APPROVED")
        self.assertNotIn("original_invoice_reference", result)

    def test_api4_rejects_pending(self):
        payload = self.credit_payload()
        payload["status"] = "PENDING"
        with self.assertRaises(IntegrationError):
            validate_credit_note_payload(payload)

    def test_api4_rejects_nonfinite_rate(self):
        payload = self.credit_payload()
        payload["items"][0]["rate"] = "NaN"
        with self.assertRaises(IntegrationError):
            validate_credit_note_payload(payload)

    def test_api3_rejects_address_owned_by_other_customer(self):
        frappe = Mock()
        frappe.db.get_value.return_value = "BS-ADDRESS-1"
        frappe.db.exists.return_value = False

        with self.assertRaises(IntegrationError) as raised:
            _get_or_create_shipping_address(
                frappe, "CUSTOMER-A", self.order_payload()
            )

        self.assertEqual(
            raised.exception.code,
            "ADDRESS_CUSTOMER_MISMATCH",
        )
        frappe.get_doc.assert_not_called()



    def test_api3_rejects_boolean_quantities_and_rates(self):
        for field in ("qty", "rate"):
            for value in (True, False):
                with self.subTest(field=field, value=value):
                    payload = self.order_payload()
                    payload["items"][0][field] = value
                    with self.assertRaises(IntegrationError) as raised:
                        validate_order_payload(payload)
                    self.assertEqual(
                        raised.exception.code,
                        "INVALID_ITEM_AMOUNT",
                    )

    def test_api4_rejects_boolean_quantities_and_rates(self):
        for field in ("qty", "rate"):
            for value in (True, False):
                with self.subTest(field=field, value=value):
                    payload = self.credit_payload()
                    payload["items"][0][field] = value
                    with self.assertRaises(IntegrationError) as raised:
                        validate_credit_note_payload(payload)
                    self.assertEqual(
                        raised.exception.code,
                        "INVALID_ITEM_AMOUNT",
                    )

if __name__ == "__main__":
    unittest.main()
