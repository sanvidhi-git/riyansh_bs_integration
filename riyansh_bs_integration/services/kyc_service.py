from __future__ import annotations


from riyansh_bs_integration.core.errors import IntegrationError, PermissionDenied


def build_kyc_payload(*, distributor_id, status, verified_at, failure_reason_code=None, failure_reason=None):
    status = (status or "").upper()
    if status not in {"PASS", "FAIL"}:
        raise IntegrationError("INVALID_KYC_STATUS", "verification_status must be PASS or FAIL", 422)
    if status == "FAIL" and (not failure_reason_code or not failure_reason):
        raise IntegrationError("MISSING_FAILURE_REASON", "FAIL requires a reason code and reason", 422)
    return {
        "distributor_id": distributor_id,
        "verification_status": status,
        "failure_reason_code": failure_reason_code if status == "FAIL" else None,
        "failure_reason": failure_reason if status == "FAIL" else None,
        "verified_at": verified_at,
    }


def approve_kyc(onboarding_name: str, decision_user: str | None = None):
    import frappe
    from frappe.utils import now_datetime
    from riyansh_bs_integration.core.outbound import queue_kyc_result

    _require_approver(frappe)
    frappe.db.sql(
        "select name from `tabBS Distributor Onboarding` where name = %s for update",
        (onboarding_name,),
    )
    doc = frappe.get_doc("BS Distributor Onboarding", onboarding_name)
    if doc.owner == (decision_user or frappe.session.user):
        raise PermissionDenied("The submitter cannot approve the same KYC record")
    if doc.kyc_status == "Passed" and doc.supplier:
        return {"supplier": doc.supplier}

    settings = frappe.get_cached_doc("BS Integration Settings")
    supplier = _create_supplier(frappe, doc, settings)
    doc.kyc_status = "Passed"
    doc.supplier = supplier
    doc.failure_reason_code = None
    doc.failure_reason = None
    doc.verified_by = decision_user or frappe.session.user
    doc.verified_at = now_datetime()
    doc.outbound_status = "Queued"
    doc.flags.kyc_service_update = True
    doc.save(ignore_permissions=True)
    frappe.enqueue(queue_kyc_result, enqueue_after_commit=True, onboarding_name=doc.name)
    return {"supplier": supplier}


def reject_kyc(onboarding_name, reason_code, reason, decision_user=None):
    import frappe
    from frappe.utils import now_datetime
    from riyansh_bs_integration.core.outbound import queue_kyc_result

    _require_approver(frappe)
    if not reason_code or not reason:
        raise IntegrationError("MISSING_FAILURE_REASON", "Failure reason code and reason are required", 422)
    frappe.db.sql(
        "select name from `tabBS Distributor Onboarding` where name = %s for update",
        (onboarding_name,),
    )
    doc = frappe.get_doc("BS Distributor Onboarding", onboarding_name)
    if doc.owner == (decision_user or frappe.session.user):
        raise PermissionDenied("The submitter cannot reject the same KYC record")
    doc.kyc_status = "Failed"
    doc.failure_reason_code, doc.failure_reason = reason_code, reason
    doc.verified_by = decision_user or frappe.session.user
    doc.verified_at = now_datetime()
    doc.outbound_status = "Queued"
    doc.flags.kyc_service_update = True
    doc.save(ignore_permissions=True)
    frappe.enqueue(queue_kyc_result, enqueue_after_commit=True, onboarding_name=doc.name)


def _require_approver(frappe):
    roles = set(frappe.get_roles(frappe.session.user))
    if not roles.intersection({"System Manager", "Riyansh KYC Approver"}):
        raise PermissionDenied("KYC decision requires Riyansh KYC Approver")


def _create_supplier(frappe, onboarding, settings):
    existing = frappe.db.get_value("Supplier", {"custom_distributor_id": onboarding.distributor_id}, "name")
    if existing:
        return existing
    supplier = frappe.get_doc({"doctype":"Supplier", "supplier_name":onboarding.member_name, "supplier_group":settings.default_supplier_group, "supplier_type":"Individual", "custom_distributor_id":onboarding.distributor_id})
    supplier.insert(ignore_permissions=True)
    return supplier.name
