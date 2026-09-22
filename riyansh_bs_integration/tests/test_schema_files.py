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


if __name__ == "__main__":
    unittest.main()
