import sys
import types
import unittest
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace

from riyansh_bs_integration.core.errors import IntegrationError
from riyansh_bs_integration.core.logging import redact
from riyansh_bs_integration.core.outbound import queue_kyc_result
from riyansh_bs_integration.services.credit_note_service import create_credit_note
from riyansh_bs_integration.services.credit_note_service import validate_credit_note_payload
from riyansh_bs_integration.services.distributor_service import REQUIRED_FILES, submit_distributor
from riyansh_bs_integration.services.order_service import _resolve_warehouse, validate_order_payload


class _Upload:
    def __init__(self, name):
        self.filename = f"{name}.png"
        self.content_type = "image/png"
        self.stream = BytesIO(b"\x89PNG\r\n\x1a\ncontrolled-test-content")


class _NewOnboarding:
    doctype = "BS Distributor Onboarding"
    name = "BS-ONB-2026-00001"

    def __init__(self):
        self.kyc_status = None
        self.inserted = False
        self.flags = SimpleNamespace(ignore_mandatory=False)

    def set(self, fieldname, value):
        setattr(self, fieldname, value)

    def set_new_name(self):
        return None

    def insert(self, ignore_permissions=False):
        missing = [field for field in REQUIRED_FILES if not getattr(self, field, None)]
        if missing and not self.flags.ignore_mandatory:
            raise AssertionError(f"insert happened before attachments were assigned: {missing}")
        self.inserted = True

    def save(self, ignore_permissions=False):
        if not self.inserted:
            raise AssertionError("save happened before insert")


class TestRuntimeRegressions(unittest.TestCase):
    def test_new_distributor_inserts_parent_before_attaching_files(self):
        document = _NewOnboarding()

        class _DB:
            @staticmethod
            def get_value(*args, **kwargs):
                return None

        fake_frappe = types.ModuleType("frappe")
        fake_frappe.db = _DB()
        fake_frappe.UniqueValidationError = type("UniqueValidationError", (Exception,), {})
        fake_frappe.get_cached_doc = lambda *args: SimpleNamespace(maximum_document_size_mb=5)
        fake_frappe.new_doc = lambda doctype: document

        file_manager = types.ModuleType("frappe.utils.file_manager")
        def save_file(filename, content, doctype, name, is_private=1):
            self.assertTrue(document.inserted, "File was attached before its parent existed")
            return SimpleNamespace(file_url=f"/private/files/{filename}")

        file_manager.save_file = save_file
        frappe_utils = types.ModuleType("frappe.utils")

        payload = {
            "distributor_id": "TEST-RM-001",
            "member_name": "Test Member",
            "mobile": "9876543210",
            "date_of_birth": "2000-01-01",
            "pan_number": "ABCDE1234F",
            "aadhaar_number": "123412341234",
            "address": {
                "address_line_1": "Test Road",
                "city": "Pune",
                "state": "Maharashtra",
                "pincode": "411001",
                "country": "India",
            },
            "bank": {
                "bank_name": "Test Bank",
                "ifsc_code": "ABCD0001234",
                "account_number": "00123456789",
            },
            "source_created_at": "2026-09-24T11:00:00+05:30",
        }
        files = {name: _Upload(name) for name in REQUIRED_FILES}

        saved_modules = {name: sys.modules.get(name) for name in ("frappe", "frappe.utils", "frappe.utils.file_manager")}
        try:
            sys.modules["frappe"] = fake_frappe
            sys.modules["frappe.utils"] = frappe_utils
            sys.modules["frappe.utils.file_manager"] = file_manager
            result, created = submit_distributor(payload, files, "COR-TEST")
        finally:
            for name, module in saved_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertTrue(created)
        self.assertEqual(result["erp_onboarding_id"], "BS-ONB-2026-00001")
        self.assertTrue(document.inserted)

    def test_outbound_event_schema_has_unique_idempotency_key_and_processing_state(self):
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        schema = json.loads(
            (root / "riyansh_bs_integration/doctype/bs_outbound_event/bs_outbound_event.json").read_text()
        )
        fields = {field["fieldname"]: field for field in schema["fields"]}
        self.assertEqual(fields["event_key"].get("unique"), 1)
        self.assertIn("Processing", fields["status"]["options"].splitlines())

    def test_outbound_source_claims_before_network_and_sends_idempotency_key(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "core/outbound.py").read_text()
        self.assertIn("def _claim_event", source)
        self.assertIn('headers["Idempotency-Key"] = event.event_key', source)
        self.assertLess(source.index("_claim_event(event_name)"), source.index("response = post("))
        self.assertIn('"status": "Processing"', source)
        self.assertIn('"status": "Retrying"', source)

    def test_logging_failure_cannot_replace_api_response(self):
        from unittest.mock import patch
        from riyansh_bs_integration.core.logging import _write_api_log_safely

        with patch(
            "riyansh_bs_integration.core.logging.write_api_log",
            side_effect=RuntimeError("controlled log failure"),
        ):
            self.assertIsNone(_write_api_log_safely(interface="TEST"))

    def test_credit_note_locks_original_invoice_and_counts_drafts(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "services/credit_note_service.py").read_text()
        self.assertIn("for update", source.lower())
        self.assertIn('"docstatus": ["in", [0, 1]]', source)

    def test_kyc_decisions_lock_onboarding_before_party_creation(self):
        from pathlib import Path

        source = (Path(__file__).resolve().parents[1] / "services/kyc_service.py").read_text()
        self.assertGreaterEqual(source.lower().count("for update"), 2)
        self.assertLess(source.index("for update"), source.index("_create_customer"))

    def test_api_log_allows_its_initial_insert_hook(self):
        fake_frappe = types.ModuleType("frappe")
        fake_frappe.throw = lambda message: (_ for _ in ()).throw(IntegrationError("LOG_LOCKED", message))
        fake_model = types.ModuleType("frappe.model")
        fake_document_module = types.ModuleType("frappe.model.document")

        class _Document:
            pass

        fake_document_module.Document = _Document
        saved_modules = {
            name: sys.modules.get(name)
            for name in ("frappe", "frappe.model", "frappe.model.document")
        }
        try:
            sys.modules["frappe"] = fake_frappe
            sys.modules["frappe.model"] = fake_model
            sys.modules["frappe.model.document"] = fake_document_module
            sys.modules.pop(
                "riyansh_bs_integration.riyansh_bs_integration.doctype.bs_api_log.bs_api_log",
                None,
            )
            from riyansh_bs_integration.riyansh_bs_integration.doctype.bs_api_log.bs_api_log import BSAPILog

            log = BSAPILog()
            log.flags = SimpleNamespace(in_insert=True)
            log.is_new = lambda: False
            log.on_update()
        finally:
            for name, module in saved_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_order_derives_delivery_date_from_order_datetime(self):
        payload = {
            "bs_order_id": "BS-1",
            "distributor_id": "RM1",
            "order_datetime": "2026-09-24T10:00:00+05:30",
            "warehouse_code": "SANGAMNER",
            "currency": "INR",
            "payment_status": "PAID",
            "payment_reference": "PAY-1",
            "shipping_address": {
                "name": "A",
                "mobile": "9876543210",
                "address_line_1": "Road",
                "city": "Pune",
                "state": "Maharashtra",
                "pincode": "411001",
            },
            "items": [
                {
                    "item_code": "ITEM-1",
                    "qty": 1,
                    "uom": "Nos",
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
        result = validate_order_payload(payload)
        self.assertEqual(result["delivery_date"], "2026-09-24")

    def test_invalid_warehouse_mapping_returns_contract_error(self):
        fake_frappe = SimpleNamespace()
        settings = SimpleNamespace(warehouse_mapping_json="{not-json")
        with self.assertRaises(IntegrationError) as raised:
            _resolve_warehouse(fake_frappe, settings, "SANGAMNER")
        self.assertEqual(raised.exception.code, "INVALID_WAREHOUSE_MAPPING")

    def test_credit_note_datetime_requires_timezone(self):
        payload = {
            "status": "APPROVED",
            "bs_credit_note_id": "CN-1",
            "bs_order_id": "BS-1",
            "distributor_id": "RM1",
            "original_invoice_reference": "SINV-1",
            "credit_note_datetime": "2026-09-24 14:00:00",
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
        with self.assertRaises(IntegrationError) as raised:
            validate_credit_note_payload(payload)
        self.assertEqual(raised.exception.code, "INVALID_DATETIME")

    def test_missing_original_invoice_is_a_contract_error(self):
        payload = {
            "status": "APPROVED",
            "bs_credit_note_id": "CN-1",
            "bs_order_id": "BS-1",
            "distributor_id": "RM1",
            "original_invoice_reference": "SINV-MISSING",
            "credit_note_datetime": "2026-09-24T14:00:00+05:30",
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

        class _DB:
            @staticmethod
            def get_value(*args, **kwargs):
                return None

            @staticmethod
            def exists(doctype, name):
                return False

        fake_frappe = types.ModuleType("frappe")
        fake_frappe.db = _DB()
        fake_mapper = types.ModuleType("erpnext.accounts.doctype.sales_invoice.mapper")
        fake_mapper.make_sales_return = lambda name: None
        module_names = (
            "frappe",
            "erpnext",
            "erpnext.accounts",
            "erpnext.accounts.doctype",
            "erpnext.accounts.doctype.sales_invoice",
            "erpnext.accounts.doctype.sales_invoice.mapper",
        )
        saved_modules = {name: sys.modules.get(name) for name in module_names}
        try:
            sys.modules["frappe"] = fake_frappe
            for name in module_names[1:-1]:
                sys.modules[name] = types.ModuleType(name)
            sys.modules[module_names[-1]] = fake_mapper
            with self.assertRaises(IntegrationError) as raised:
                create_credit_note(payload, "COR-TEST")
        finally:
            for name, module in saved_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module
        self.assertEqual(raised.exception.code, "INVOICE_NOT_FOUND")

    def test_changed_kyc_decision_creates_a_new_outbound_event(self):
        onboarding = SimpleNamespace(
            name="BS-ONB-2026-00001",
            distributor_id="TEST-RM-001",
            kyc_status="Failed",
            verified_at=datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc),
            customer=None,
            supplier=None,
            failure_reason_code="DOC_INVALID",
            failure_reason="Controlled failure",
            correlation_id="COR-TEST",
        )
        settings = SimpleNamespace(
            integration_enabled=True,
            kyc_outbound_enabled=True,
            kyc_result_url="https://bs.example.test/kyc-result",
        )
        inserted = []

        class _DB:
            @staticmethod
            def get_value(doctype, filters, fieldname):
                if filters.get("event_key"):
                    return None
                return "BS-EVT-OLD"

        class _Event(SimpleNamespace):
            def insert(self, ignore_permissions=False):
                self.name = "BS-EVT-NEW"
                inserted.append(self)

        fake_frappe = types.ModuleType("frappe")
        fake_frappe.db = _DB()
        fake_frappe.UniqueValidationError = type("UniqueValidationError", (Exception,), {})
        fake_frappe.get_doc = lambda *args: (
            onboarding
            if args == ("BS Distributor Onboarding", onboarding.name)
            else _Event(**args[0])
        )
        fake_frappe.get_cached_doc = lambda *args: settings
        fake_frappe.enqueue = lambda *args, **kwargs: None
        fake_utils = types.ModuleType("frappe.utils")
        fake_utils.get_datetime = lambda value: value
        fake_utils.get_system_timezone = lambda: "UTC"
        saved_modules = {name: sys.modules.get(name) for name in ("frappe", "frappe.utils")}
        try:
            sys.modules["frappe"] = fake_frappe
            sys.modules["frappe.utils"] = fake_utils
            event_name = queue_kyc_result(onboarding.name)
        finally:
            for name, module in saved_modules.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

        self.assertEqual(event_name, "BS-EVT-NEW")
        self.assertEqual(len(inserted), 1)

    def test_logs_mask_pan_and_mobile(self):
        result = redact({"pan_number": "ABCDE1234F", "mobile": "9876543210"})
        self.assertNotEqual(result["pan_number"], "ABCDE1234F")
        self.assertNotEqual(result["mobile"], "9876543210")
        self.assertTrue(result["mobile"].endswith("3210"))


if __name__ == "__main__":
    unittest.main()
