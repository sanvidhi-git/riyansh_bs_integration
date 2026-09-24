from __future__ import annotations

import json
from datetime import datetime

from riyansh_bs_integration.core.errors import IntegrationError, PermissionDenied


def build_kyc_payload(*, distributor_id, status, verified_at, customer=None, supplier=None, failure_reason_code=None, failure_reason=None):
    status = (status or "").upper()
    if status not in {"PASS", "FAIL"}:
        raise IntegrationError("INVALID_KYC_STATUS", "verification_status must be PASS or FAIL", 422)
    if status == "PASS" and (not customer or not supplier):
        raise IntegrationError("MISSING_PARTY", "PASS requires Customer and Supplier IDs", 422)
    if status == "FAIL" and (not failure_reason_code or not failure_reason):
        raise IntegrationError("MISSING_FAILURE_REASON", "FAIL requires a reason code and reason", 422)
    return {
        "distributor_id": distributor_id,
        "erp_customer_id": customer if status == "PASS" else None,
        "erp_supplier_id": supplier if status == "PASS" else None,
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
    if doc.kyc_status == "Passed" and doc.customer and doc.supplier:
        return {"customer": doc.customer, "supplier": doc.supplier}
    settings = frappe.get_cached_doc("BS Integration Settings")
    customer = _create_customer(frappe, doc, settings)
    supplier = _create_supplier(frappe, doc, settings)
    _create_party_link(frappe, customer, supplier)
    _create_shared_address_and_contact(frappe, doc, customer, supplier)
    doc.kyc_status = "Passed"
    doc.customer, doc.supplier = customer, supplier
    doc.verified_by = decision_user or frappe.session.user
    doc.verified_at = now_datetime()
    doc.outbound_status = "Queued"
    doc.flags.kyc_service_update = True
    doc.save(ignore_permissions=True)
    frappe.enqueue(queue_kyc_result, enqueue_after_commit=True, onboarding_name=doc.name)
    return {"customer": customer, "supplier": supplier}


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


def _create_customer(frappe, onboarding, settings):
    existing = frappe.db.get_value("Customer", {"custom_distributor_id": onboarding.distributor_id}, "name")
    if existing:
        return existing
    customer = frappe.get_doc({"doctype":"Customer", "customer_name":onboarding.member_name, "customer_type":"Individual", "customer_group":settings.default_customer_group, "territory":settings.default_territory, "custom_distributor_id":onboarding.distributor_id})
    customer.insert(ignore_permissions=True)
    return customer.name


def _create_supplier(frappe, onboarding, settings):
    existing = frappe.db.get_value("Supplier", {"custom_distributor_id": onboarding.distributor_id}, "name")
    if existing:
        return existing
    supplier = frappe.get_doc({"doctype":"Supplier", "supplier_name":onboarding.member_name, "supplier_group":settings.default_supplier_group, "supplier_type":"Individual", "custom_distributor_id":onboarding.distributor_id})
    supplier.insert(ignore_permissions=True)
    return supplier.name


def _create_party_link(frappe, customer, supplier):
    filters = {"primary_role":"Customer", "primary_party":customer, "secondary_role":"Supplier", "secondary_party":supplier}
    if frappe.db.exists("Party Link", filters):
        return
    frappe.get_doc({"doctype":"Party Link", **filters}).insert(ignore_permissions=True)


def _create_shared_address_and_contact(frappe, onboarding, customer, supplier):
    links = [
        {"link_doctype": "Customer", "link_name": customer},
        {"link_doctype": "Supplier", "link_name": supplier},
    ]
    address = json.loads(onboarding.address_json or "{}")
    if not frappe.db.exists("Address", {"address_title": onboarding.distributor_id}):
        values = {
            "doctype": "Address",
            "address_title": onboarding.distributor_id,
            "address_type": "Billing",
            "address_line1": address.get("address_line_1"),
            "address_line2": address.get("address_line_2"),
            "city": address.get("city"),
            "state": address.get("state"),
            "pincode": address.get("pincode"),
            "country": address.get("country") or "India",
            "phone": onboarding.mobile,
            "email_id": onboarding.email,
            "is_primary_address": 1,
            "is_shipping_address": 1,
            "links": links,
        }
        frappe.get_doc(values).insert(ignore_permissions=True)
    if not frappe.db.exists("Contact", {"first_name": onboarding.member_name, "mobile_no": onboarding.mobile}):
        frappe.get_doc({
            "doctype": "Contact",
            "first_name": onboarding.member_name,
            "email_ids": [{"email_id": onboarding.email, "is_primary": 1}] if onboarding.email else [],
            "phone_nos": [{"phone": onboarding.mobile, "is_primary_mobile_no": 1}],
            "links": links,
        }).insert(ignore_permissions=True)
