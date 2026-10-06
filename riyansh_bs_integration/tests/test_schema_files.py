import json
from pathlib import Path
import unittest


ROOT = Path(__file__).parents[1]


class TestSchemaFiles(unittest.TestCase):
    def test_required_doctypes_exist_and_are_valid_json(self):
        base = ROOT / "riyansh_bs_integration" / "doctype"
        for name in (
            "bs_integration_settings",
            "bs_distributor_onboarding",
            "bs_api_log",
            "bs_outbound_event",
        ):
            schema = json.loads((base / name / f"{name}.json").read_text(encoding="utf-8"))
            self.assertEqual(schema["doctype"], "DocType")
            self.assertTrue(schema["name"].startswith("BS "))

    def test_external_reference_fields_are_defined_once(self):
        from riyansh_bs_integration.custom_fields import CUSTOM_FIELDS

        expected = {
            ("Customer", "custom_distributor_id"),
            ("Supplier", "custom_distributor_id"),
            ("Sales Order", "custom_bs_order_id"),
            ("Sales Invoice", "custom_bs_credit_note_id"),
        }
        actual = {
            (doctype, field["fieldname"])
            for doctype, fields in CUSTOM_FIELDS.items()
            for field in fields
        }
        self.assertTrue(expected.issubset(actual))
        self.assertEqual(len(actual), sum(len(fields) for fields in CUSTOM_FIELDS.values()))

    def test_api1_schema_matches_current_required_contract(self):
        schema = json.loads(
            (ROOT / "riyansh_bs_integration/doctype/bs_distributor_onboarding/bs_distributor_onboarding.json").read_text()
        )
        fields = {field["fieldname"]: field for field in schema["fields"]}
        for field in ("distributor_id", "member_name", "mobile", "email", "date_of_birth", "pan_number", "aadhaar_number", "pan_card", "aadhaar_front", "aadhaar_back", "cancelled_cheque"):
            if field in ("pan_card", "aadhaar_front", "aadhaar_back", "cancelled_cheque"):
                self.assertNotEqual(fields[field].get("reqd"), 1, field)
            else:
                self.assertEqual(fields[field].get("reqd"), 1, field)
        for field in ("address_json", "bank_name", "ifsc_code", "account_number", "source_created_at"):
            self.assertNotEqual(fields[field].get("reqd"), 1, field)



if __name__ == "__main__":
    unittest.main()
