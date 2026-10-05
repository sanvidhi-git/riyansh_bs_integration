import unittest
from io import BytesIO
from types import SimpleNamespace

from riyansh_bs_integration.core.errors import IntegrationError
from riyansh_bs_integration.services.credit_note_service import validate_credit_note_payload
from riyansh_bs_integration.services.distributor_service import validate_distributor_payload, validate_documents
from riyansh_bs_integration.services.kyc_service import build_kyc_payload
from riyansh_bs_integration.services.order_service import validate_order_payload
from riyansh_bs_integration.services.credit_note_service import remaining_return_quantity


class TestPayloadContracts(unittest.TestCase):
    @staticmethod
    def valid_order_payload():
        return {
            "bs_order_id": "BS-1",
            "distributor_id": "RM1",
            "order_datetime": "2026-09-22T10:00:00+05:30",
            "warehouse_code": "SANGAMNER",
            "currency": "INR",
            "payment_status": "PAID",
            "payment_reference": "PAY-1",
            "shipping_address": {
                "name": "A",
                "mobile": "9876543210",
                "address_line_1": "Road",
                "city": "Mouda",
                "state": "Maharashtra",
                "pincode": "441104",
            },
            "items": [
                {
                    "item_code": "ITEM-1",
                    "qty": 1,
                    "uom": "Nos",
                    "rate": 100,
                    "discount_amount": 0,
                    "taxable_amount": 100,
                    "tax_amount": 0,
                    "line_total": 100,
                }
            ],
            "taxable_value": 100,
            "total_tax": 0,
            "shipping_amount": 0,
            "grand_total": 100,
        }

    @staticmethod
    def valid_credit_payload():
        return {
            "status": "APPROVED",
            "bs_credit_note_id": "CN-1",
            "bs_order_id": "BS-1",
            "distributor_id": "RM1",
            "original_invoice_reference": "SINV-1",
            "credit_note_datetime": "2026-09-22T10:00:00+05:30",
            "reason_code": "CUSTOMER_RETURN",
            "reason": "Test return",
            "warehouse_code": "SANGAMNER",
            "items": [
                {
                    "item_code": "ITEM-1",
                    "qty": 1,
                    "rate": 100,
                    "taxable_amount": 100,
                    "tax_amount": 0,
                    "line_total": 100,
                }
            ],
            "taxable_value": 100,
            "total_tax": 0,
            "grand_total": 100,
        }

    def test_valid_distributor_is_normalized(self):
        payload = {
            "distributor_id": " RM6110738 ",
            "member_name": " Sample Member ",
            "mobile": "9876543210",
            "email": "member@example.com",
            "date_of_birth": "2000-08-03",
            "pan_number": "abcde1234f",
            "aadhaar_number": "1234 1234 1234",
        }
        result = validate_distributor_payload(payload)
        self.assertEqual(result["distributor_id"], "RM6110738")
        self.assertEqual(result["member_name"], "Sample Member")
        self.assertEqual(result["email"], "member@example.com")
        self.assertEqual(result["pan_number"], "ABCDE1234F")
        self.assertNotIn("address", result)
        self.assertNotIn("bank", result)
        self.assertNotIn("source_created_at", result)

    def test_distributor_optional_legacy_fields_are_accepted_when_present(self):
        payload = {
            "distributor_id": "RM6110738",
            "member_name": "Sample Member",
            "mobile": "9876543210",
            "email": "member@example.com",
            "date_of_birth": "2000-08-03",
            "pan_number": "ABCDE1234F",
            "aadhaar_number": "123412341234",
            "address": {"address_line_1": "Road", "city": "Mouda", "state": "Maharashtra", "pincode": "441104", "country": "India"},
            "bank": {"bank_name": "Sample Bank", "ifsc_code": "ABCD0001234", "account_number": "0012345"},
            "source_created_at": "2026-09-22T09:30:00+05:30",
        }
        result = validate_distributor_payload(payload)
        self.assertEqual(result["address"]["pincode"], "441104")
        self.assertEqual(result["bank"]["account_number"], "0012345")
        self.assertEqual(result["source_created_at"], "2026-09-22T09:30:00+05:30")

    def test_distributor_requires_exact_current_contract_fields(self):
        base = {
            "distributor_id": "RM6110738",
            "member_name": "Sample Member",
            "mobile": "9876543210",
            "email": "member@example.com",
            "date_of_birth": "2000-08-03",
            "pan_number": "ABCDE1234F",
            "aadhaar_number": "123412341234",
        }
        for field in base:
            with self.subTest(field=field):
                payload = dict(base)
                payload.pop(field)
                with self.assertRaises(IntegrationError) as raised:
                    validate_distributor_payload(payload)
                self.assertEqual(raised.exception.code, "REQUIRED_FIELD")
                self.assertEqual(raised.exception.field, field)

    def test_order_rejects_mismatched_grand_total(self):
        payload = {
            "bs_order_id": "BS-1", "distributor_id": "RM1", "order_datetime": "2026-09-22T10:00:00+05:30",
            "warehouse_code": "SANGAMNER", "currency": "INR", "payment_status": "PAID", "payment_reference": "PAY-1",
            "shipping_address": {"name":"A","mobile":"9876543210","address_line_1":"Road","city":"Mouda","state":"Maharashtra","pincode":"441104"},
            "items": [{"item_code":"ITEM-1","qty":2,"uom":"Nos","rate":100,"discount_amount":0,"taxable_amount":200,"gst_rate":18,"tax_amount":36,"line_total":236}],
            "subtotal":200,"discount_amount":0,"taxable_value":200,"total_tax":36,"shipping_amount":0,"grand_total":999
        }
        with self.assertRaises(IntegrationError):
            validate_order_payload(payload)

    def test_order_rejects_non_finite_amounts_as_contract_error(self):
        payload = self.valid_order_payload()
        payload["items"][0]["rate"] = "NaN"
        with self.assertRaises(IntegrationError) as raised:
            validate_order_payload(payload)
        self.assertEqual(raised.exception.code, "INVALID_AMOUNT")

    def test_order_rejects_negative_shipping_amount(self):
        payload = self.valid_order_payload()
        payload["shipping_amount"] = -10
        payload["grand_total"] = 90
        with self.assertRaises(IntegrationError) as raised:
            validate_order_payload(payload)
        self.assertEqual(raised.exception.code, "INVALID_AMOUNT")

    def test_credit_note_requires_approved_status(self):
        with self.assertRaises(IntegrationError):
            validate_credit_note_payload({"status": "PENDING"})

    def test_credit_note_rejects_non_finite_amounts_as_contract_error(self):
        payload = self.valid_credit_payload()
        payload["items"][0]["tax_amount"] = "NaN"
        with self.assertRaises(IntegrationError) as raised:
            validate_credit_note_payload(payload)
        self.assertEqual(raised.exception.code, "INVALID_AMOUNT")

    def test_credit_note_rejects_invalid_header_totals_as_contract_error(self):
        payload = self.valid_credit_payload()
        payload["grand_total"] = "not-a-number"
        with self.assertRaises(IntegrationError) as raised:
            validate_credit_note_payload(payload)
        self.assertEqual(raised.exception.code, "INVALID_AMOUNT")

    def test_kyc_pass_payload_has_only_agreed_fields(self):
        result = build_kyc_payload(
            distributor_id="RM1",
            status="PASS",
            verified_at="2026-09-22T11:30:00+05:30",
        )
        self.assertEqual(
            set(result),
            {"distributor_id", "verification_status", "failure_reason_code", "failure_reason", "verified_at"},
        )
        self.assertEqual(result["verification_status"], "PASS")
        self.assertIsNone(result["failure_reason_code"])
        self.assertIsNone(result["failure_reason"])
        self.assertNotIn("erp_customer_id", result)
        self.assertNotIn("erp_supplier_id", result)

    def test_kyc_fail_requires_reason(self):
        with self.assertRaises(IntegrationError):
            build_kyc_payload(distributor_id="RM1", status="FAIL", verified_at="2026-09-22T11:30:00+05:30")

    def test_kyc_fail_payload_has_only_agreed_fields_and_reason(self):
        result = build_kyc_payload(
            distributor_id="RM1",
            status="FAIL",
            verified_at="2026-09-22T11:30:00+05:30",
            failure_reason_code="PAN_MISMATCH",
            failure_reason="PAN details do not match",
        )
        self.assertEqual(
            set(result),
            {"distributor_id", "verification_status", "failure_reason_code", "failure_reason", "verified_at"},
        )
        self.assertEqual(result["verification_status"], "FAIL")
        self.assertEqual(result["failure_reason_code"], "PAN_MISMATCH")
        self.assertEqual(result["failure_reason"], "PAN details do not match")

    def test_cumulative_return_quantity_never_goes_below_zero(self):
        self.assertEqual(remaining_return_quantity(10, [2, 3]), 5)
        self.assertEqual(remaining_return_quantity(5, [2, 4]), 0)

    def test_document_mime_must_match_file_signature(self):
        files = {
            name: SimpleNamespace(filename=f"{name}.png", content_type="image/png", stream=BytesIO(b"not-a-png"))
            for name in ("pan_card", "aadhaar_front", "aadhaar_back", "cancelled_cheque")
        }
        with self.assertRaises(IntegrationError):
            validate_documents(files)


if __name__ == "__main__":
    unittest.main()
