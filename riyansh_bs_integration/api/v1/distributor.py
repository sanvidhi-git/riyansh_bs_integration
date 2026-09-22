import json

import frappe

from riyansh_bs_integration.core.errors import IntegrationError
from riyansh_bs_integration.core.logging import integration_endpoint
from riyansh_bs_integration.core.responses import success
from riyansh_bs_integration.services.distributor_service import submit_distributor


@frappe.whitelist(methods=["POST"])
@integration_endpoint("BS-01", reference_key="distributor_id")
def submit(payload=None, correlation_id=None):
    try:
        parsed = frappe.parse_json(payload or frappe.form_dict.get("payload") or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise IntegrationError("INVALID_JSON", "payload is not valid JSON", 400, field="payload") from exc
    files = dict(frappe.request.files or {})
    data, created = submit_distributor(parsed, files, correlation_id)
    frappe.local.response.http_status_code = 201 if created else 200
    return success(data, "Distributor submitted for KYC verification", correlation_id)

