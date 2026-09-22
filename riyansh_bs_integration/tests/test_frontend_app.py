import importlib
import json
import sys
import types
import unittest
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
MODULE_ROOT = APP_ROOT / "riyansh_bs_integration"


class TestFrontendAppRegistration(unittest.TestCase):
    def test_hooks_register_app_tile_and_workspace_route(self):
        hooks = importlib.import_module("riyansh_bs_integration.hooks")

        self.assertEqual(hooks.app_logo_url, "/assets/riyansh_bs_integration/images/logo.svg")
        self.assertEqual(hooks.app_home, "/desk/riyansh-bs-integration")
        self.assertEqual(hooks.required_apps, ["erpnext"])
        self.assertEqual(
            hooks.add_to_apps_screen,
            [
                {
                    "name": "riyansh_bs_integration",
                    "logo": "/assets/riyansh_bs_integration/images/logo.svg",
                    "title": "Riyansh BS Integration",
                    "route": "/desk/riyansh-bs-integration",
                    "has_permission": "riyansh_bs_integration.utils.permissions.has_app_permission",
                }
            ],
        )

    def test_workspace_exposes_only_the_four_integration_admin_records(self):
        workspace_path = (
            MODULE_ROOT
            / "workspace"
            / "riyansh_bs_integration"
            / "riyansh_bs_integration.json"
        )
        workspace = json.loads(workspace_path.read_text(encoding="utf-8"))

        self.assertEqual(workspace["doctype"], "Workspace")
        self.assertEqual(workspace["module"], "Riyansh BS Integration")
        self.assertEqual(workspace["label"], "Riyansh BS Integration")
        self.assertEqual(workspace["app"], "riyansh_bs_integration")
        self.assertEqual(workspace["public"], 1)

        links = {
            link["link_to"]
            for link in workspace["links"]
            if link.get("type") == "Link"
        }
        self.assertEqual(
            links,
            {
                "BS Distributor Onboarding",
                "BS API Log",
                "BS Outbound Event",
                "BS Integration Settings",
            },
        )

    def test_desktop_icon_opens_the_workspace(self):
        icon_path = APP_ROOT / "desktop_icon" / "riyansh_bs_integration.json"
        icon = json.loads(icon_path.read_text(encoding="utf-8"))

        self.assertEqual(icon["doctype"], "Desktop Icon")
        self.assertEqual(icon["app"], "riyansh_bs_integration")
        self.assertEqual(icon["link"], "/desk/riyansh-bs-integration")
        self.assertEqual(icon["logo_url"], "/assets/riyansh_bs_integration/images/logo.svg")
        self.assertEqual(icon["standard"], 1)


class TestFrontendAppPermissions(unittest.TestCase):
    def _permission_result(self, user, roles):
        fake_frappe = types.ModuleType("frappe")
        fake_frappe.session = types.SimpleNamespace(user=user)
        fake_frappe.get_roles = lambda: roles

        previous = sys.modules.get("frappe")
        sys.modules["frappe"] = fake_frappe
        try:
            module_name = "riyansh_bs_integration.utils.permissions"
            sys.modules.pop(module_name, None)
            permissions = importlib.import_module(module_name)
            return permissions.has_app_permission()
        finally:
            sys.modules.pop("riyansh_bs_integration.utils.permissions", None)
            if previous is None:
                sys.modules.pop("frappe", None)
            else:
                sys.modules["frappe"] = previous

    def test_authorized_desk_roles_can_see_app(self):
        for role in (
            "System Manager",
            "Riyansh KYC Verifier",
            "Riyansh KYC Approver",
        ):
            with self.subTest(role=role):
                self.assertTrue(self._permission_result("user@example.com", [role]))

        self.assertTrue(self._permission_result("Administrator", []))

    def test_api_only_and_unrelated_users_cannot_see_app(self):
        self.assertFalse(
            self._permission_result(
                "integration@example.com", ["Riyansh BS Integration"]
            )
        )
        self.assertFalse(self._permission_result("user@example.com", ["Sales User"]))


if __name__ == "__main__":
    unittest.main()
