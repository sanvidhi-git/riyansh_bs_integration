import frappe

from riyansh_bs_integration.core.logging import integration_endpoint
from riyansh_bs_integration.core.responses import success
from riyansh_bs_integration.services.credit_note_service import create_credit_note


@frappe.whitelist(methods=["POST"])
@integration_endpoint("BS-04", reference_key="bs_credit_note_id")
def create(payload=None, correlation_id=None):
    parsed = frappe.parse_json(payload or frappe.request.get_json(silent=True) or {})
    data, created = create_credit_note(parsed, correlation_id)
    frappe.local.response.http_status_code = 201 if created else 200
    return success(data, "Credit Note created" if created else "Credit Note already exists", correlation_id)
