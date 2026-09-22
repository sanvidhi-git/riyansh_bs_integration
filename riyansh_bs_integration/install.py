from riyansh_bs_integration.custom_fields import CUSTOM_FIELDS

ROLES = (
    ("Riyansh BS Integration", 0),
    ("Riyansh KYC Verifier", 1),
    ("Riyansh KYC Approver", 1),
)


def after_install():
    create_roles()
    create_custom_fields()


def after_migrate():
    create_roles()
    create_custom_fields()


def create_roles():
    import frappe

    for role_name, desk_access in ROLES:
        if frappe.db.exists("Role", role_name):
            continue
        role = frappe.new_doc("Role")
        role.role_name = role_name
        role.desk_access = desk_access
        role.insert(ignore_permissions=True)


def create_custom_fields():
    from frappe.custom.doctype.custom_field.custom_field import create_custom_fields as create

    create(CUSTOM_FIELDS, update=False)

