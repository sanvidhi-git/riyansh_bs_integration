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
    def test_valid_distributor_is_normalized(self):
        payload = {
            "distributor_id": " RM6110738 ", "member_name": " Sample Member ",
            "mobile": "9876543210", "date_of_birth": "2000-08-03",
            "pan_number": "abcde1234f", "aadhaar_number": "1234 1234 1234",
            "address": {"address_line_1": "Road", "city": "Mouda", "state": "Maharashtra", "pincode": "441104", "country": "India"},
            "bank": {"bank_name": "Sample Bank", "ifsc_code": "abcd0001234", "account_number": "0012345"},
            "source_created_at": "2026-09-22T09:30:00+05:30"
        }
        result = validate_distributor_payload(payload)
        self.assertEqual(result["distributor_id"], "RM6110738")
        self.assertEqual(result["pan_number"], "ABCDE1234F")
        self.assertEqual(result["bank"]["account_number"], "0012345")

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

    def test_credit_note_requires_approved_status(self):
        with self.assertRaises(IntegrationError):
            validate_credit_note_payload({"status": "PENDING"})

    def test_kyc_pass_payload_has_both_party_ids(self):
        result = build_kyc_payload(
            distributor_id="RM1", status="PASS", verified_at="2026-09-22T11:30:00+05:30",
            customer="CUST-1", supplier="SUPP-1"
        )
        self.assertEqual(result["erp_customer_id"], "CUST-1")
        self.assertEqual(result["erp_supplier_id"], "SUPP-1")
        self.assertIsNone(result["failure_reason"])

    def test_kyc_fail_requires_reason(self):
        with self.assertRaises(IntegrationError):
            build_kyc_payload(distributor_id="RM1", status="FAIL", verified_at="2026-09-22T11:30:00+05:30")

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
