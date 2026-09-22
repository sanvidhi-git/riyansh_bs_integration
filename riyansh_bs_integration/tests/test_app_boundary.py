from importlib import import_module
from pathlib import Path
import unittest


class TestAppBoundary(unittest.TestCase):
    def test_app_metadata_is_importable(self):
        package = import_module("riyansh_bs_integration")
        hooks = import_module("riyansh_bs_integration.hooks")

        self.assertEqual(package.__version__, "0.1.0")
        self.assertEqual(hooks.app_name, "riyansh_bs_integration")
        self.assertEqual(hooks.app_title, "Riyansh BS Integration")

    def test_new_app_does_not_import_old_app(self):
        root = Path(__file__).parents[2]
        production_files = [
            path for path in root.rglob("*.py")
            if "tests" not in path.parts
        ]
        python_text = "\n".join(path.read_text(encoding="utf-8") for path in production_files)

        self.assertNotIn("from riyansh_integration", python_text)
        self.assertNotIn("import riyansh_integration", python_text)


if __name__ == "__main__":
    unittest.main()
