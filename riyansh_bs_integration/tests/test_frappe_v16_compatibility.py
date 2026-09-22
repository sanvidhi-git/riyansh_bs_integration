from pathlib import Path
import unittest


ROOT = Path(__file__).parents[2]


class TestFrappeV16Compatibility(unittest.TestCase):
    def test_credit_note_uses_v16_mapper_import(self):
        source = (ROOT / "riyansh_bs_integration/services/credit_note_service.py").read_text()
        self.assertIn(
            "from erpnext.accounts.doctype.sales_invoice.mapper import make_sales_return",
            source,
        )
        self.assertNotIn(
            "from erpnext.accounts.doctype.sales_invoice.sales_invoice import make_sales_return",
            source,
        )

    def test_sales_order_does_not_call_removed_set_taxes_method(self):
        source = (ROOT / "riyansh_bs_integration/services/order_service.py").read_text()
        self.assertNotIn("doc.set_taxes()", source)

    def test_bench_dependencies_are_declared(self):
        pyproject = (ROOT / "pyproject.toml").read_text()
        self.assertIn("[tool.bench.frappe-dependencies]", pyproject)
        self.assertIn('frappe = ">=16.0.0,<17.0.0"', pyproject)
        self.assertIn('erpnext = ">=16.0.0,<17.0.0"', pyproject)


if __name__ == "__main__":
    unittest.main()
