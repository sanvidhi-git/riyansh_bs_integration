import frappe

from riyansh_bs_integration.core.logging import integration_endpoint
from riyansh_bs_integration.core.responses import success
from riyansh_bs_integration.services.order_service import create_sales_order


@frappe.whitelist(methods=["POST"])
@integration_endpoint("BS-03", reference_key="bs_order_id")
def create(payload=None, correlation_id=None):
    parsed = frappe.parse_json(payload or frappe.request.get_json(silent=True) or {})
    data, created = create_sales_order(parsed, correlation_id)
    frappe.local.response.http_status_code = 201 if created else 200
    return success(data, "Sales Order created" if created else "Sales Order already exists", correlation_id)

