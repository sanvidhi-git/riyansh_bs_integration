import ast
import unittest
from pathlib import Path


BASE = (
    Path(__file__).resolve().parents[1]
    / "services"
)


class TestDuplicateRaceHandling(unittest.TestCase):

    def test_both_services_catch_database_duplicate_errors(self):
        for filename in (
            "order_service.py",
            "credit_note_service.py",
        ):
            with self.subTest(filename=filename):
                source = (BASE / filename).read_text()
                tree = ast.parse(source)

                handlers = [
                    node for node in ast.walk(tree)
                    if isinstance(node, ast.ExceptHandler)
                    and node.type is not None
                    and "DuplicateEntryError" in ast.unparse(node.type)
                ]

                self.assertEqual(len(handlers), 1)

                handler = handlers[0]
                handler_source = ast.get_source_segment(
                    source, handler
                )

                self.assertIn(
                    "frappe.db.rollback()",
                    handler_source,
                )
                self.assertIn(
                    "custom_bs_request_fingerprint",
                    handler_source,
                )
                self.assertIn(
                    "if not winner:",
                    handler_source,
                )

    def test_order_and_credit_note_unique_indexes_defined(self):
        source = (
            Path(__file__).resolve().parents[1]
            / "custom_fields.py"
        ).read_text()

        for field in (
            "custom_bs_order_id",
            "custom_bs_credit_note_id",
        ):
            with self.subTest(field=field):
                self.assertIn(
                    f'"fieldname": "{field}"',
                    source,
                )

        self.assertGreaterEqual(
            source.count('"unique": 1'),
            2,
        )


if __name__ == "__main__":
    unittest.main()
