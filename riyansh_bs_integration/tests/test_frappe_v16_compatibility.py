from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]


class TestFrappeV16Compatibility(unittest.TestCase):
    def test_credit_note_uses_v16_sales_invoice_import(self):
        source = (
            ROOT / "riyansh_bs_integration/services/credit_note_service.py"
        ).read_text()
        self.assertIn('frappe.new_doc("Sales Invoice")', source)
        self.assertIn("credit.is_return = 1", source)
        self.assertIn("credit.return_against = None", source)
        self.assertIn("set_document_gst(", source)
        self.assertNotIn("make_sales_return(", source)

    def test_sales_order_uses_v16_tax_template_helper(self):
        order = (
            ROOT / "riyansh_bs_integration/services/order_service.py"
        ).read_text()
        gst = (
            ROOT / "riyansh_bs_integration/services/gst_service.py"
        ).read_text()
        self.assertIn("set_document_gst(", order)
        self.assertIn("get_taxes_and_charges", gst)
        self.assertIn("Sales Taxes and Charges Template", gst)

    def test_bench_dependencies_are_declared(self):
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn("[tool.bench.frappe-dependencies]", pyproject)
        self.assertIn('frappe = ">=16.0.0,<17.0.0"', pyproject)
        self.assertIn('erpnext = ">=16.0.0,<17.0.0"', pyproject)


if __name__ == "__main__":
    unittest.main()
