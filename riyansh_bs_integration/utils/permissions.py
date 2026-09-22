import frappe


DESK_ROLES = {
    "System Manager",
    "Riyansh KYC Verifier",
    "Riyansh KYC Approver",
}


def has_app_permission():
    """Return whether the current user may see the integration app tile."""
    if frappe.session.user == "Administrator":
        return True
    return bool(DESK_ROLES & set(frappe.get_roles()))
