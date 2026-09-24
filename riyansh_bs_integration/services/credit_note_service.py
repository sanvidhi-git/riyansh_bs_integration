from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from riyansh_bs_integration.core.errors import ConflictError, IntegrationError
from riyansh_bs_integration.core.validation import require, request_fingerprint


def remaining_return_quantity(original_qty, returned_quantities):
    remaining = Decimal(str(original_qty)) - sum(
        (abs(Decimal(str(qty))) for qty in returned_quantities), Decimal("0")
    )
    return max(remaining, Decimal("0"))


def validate_credit_note_payload(payload):
    if not isinstance(payload, dict):
        raise IntegrationError("INVALID_JSON", "Request body must be a JSON object", 400)
    if str(payload.get("status", "")).upper() != "APPROVED":
        raise IntegrationError("RETURN_NOT_APPROVED", "Only APPROVED credit notes are accepted", 422, field="status")
    for field in ("bs_credit_note_id", "bs_order_id", "distributor_id", "original_invoice_reference", "credit_note_datetime", "reason_code", "reason", "warehouse_code", "items", "taxable_value", "total_tax", "grand_total"):
        require(payload, field)
    try:
        credit_note_datetime = datetime.fromisoformat(str(payload["credit_note_datetime"]))
        if credit_note_datetime.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise IntegrationError(
            "INVALID_DATETIME",
            "credit_note_datetime must include timezone",
            422,
            field="credit_note_datetime",
        ) from exc
    if not isinstance(payload["items"], list) or not payload["items"]:
        raise IntegrationError("EMPTY_ITEMS", "At least one credit-note item is required", 422, field="items")
    taxable = tax = total = Decimal("0")
    for index, item in enumerate(payload["items"]):
        require(item, "item_code")
        try:
            qty = Decimal(str(require(item, "qty")))
            rate = Decimal(str(require(item, "rate")))
            item_taxable = Decimal(str(require(item, "taxable_amount")))
            item_tax = Decimal(str(require(item, "tax_amount")))
            item_total = Decimal(str(require(item, "line_total")))
        except InvalidOperation as exc:
            raise IntegrationError("INVALID_AMOUNT", "Credit-note amount is invalid", 422, field=f"items[{index}]") from exc
        if qty <= 0 or rate < 0 or abs(item_total - item_taxable - item_tax) > Decimal("0.02"):
            raise IntegrationError("CREDIT_LINE_MISMATCH", "Credit-note line does not reconcile", 422, field=f"items[{index}]")
        taxable += item_taxable; tax += item_tax; total += item_total
    if abs(taxable - Decimal(str(payload["taxable_value"]))) > Decimal("0.02") or abs(tax - Decimal(str(payload["total_tax"]))) > Decimal("0.02") or abs(total - Decimal(str(payload["grand_total"]))) > Decimal("0.02"):
        raise IntegrationError("CREDIT_TOTAL_MISMATCH", "Credit-note totals do not reconcile", 422)
    return payload


def create_credit_note(payload, correlation_id):
    import frappe
    from erpnext.accounts.doctype.sales_invoice.mapper import make_sales_return

    payload = validate_credit_note_payload(payload)
    fingerprint = request_fingerprint(payload)
    existing = frappe.db.get_value("Sales Invoice", {"custom_bs_credit_note_id":payload["bs_credit_note_id"]}, ["name", "custom_bs_request_fingerprint"], as_dict=True)
    if existing:
        if existing.custom_bs_request_fingerprint != fingerprint:
            raise ConflictError("CREDIT_NOTE_ID_CONFLICT", "The BS credit-note ID already exists with different data", field="bs_credit_note_id")
        return {"credit_note": existing.name, "duplicate": True}, False
    if not frappe.db.exists("Sales Invoice", payload["original_invoice_reference"]):
        raise IntegrationError(
            "INVOICE_NOT_FOUND",
            "Original Sales Invoice does not exist",
            404,
            field="original_invoice_reference",
        )
    # Serialize all returns against the same invoice. This prevents two
    # concurrent requests with different BS IDs from both consuming the same
    # remaining quantity.
    frappe.db.sql(
        "select name from `tabSales Invoice` where name = %s for update",
        (payload["original_invoice_reference"],),
    )
    original = frappe.get_doc("Sales Invoice", payload["original_invoice_reference"])
    if original.docstatus != 1:
        raise IntegrationError("INVOICE_NOT_SUBMITTED", "Original Sales Invoice must be submitted", 422)
    if original.custom_bs_order_id and original.custom_bs_order_id != payload["bs_order_id"]:
        raise IntegrationError("ORDER_REFERENCE_MISMATCH", "BS order ID does not match the original invoice", 422, field="bs_order_id")
    customer_distributor = frappe.db.get_value("Customer", original.customer, "custom_distributor_id")
    if customer_distributor != payload["distributor_id"]:
        raise IntegrationError("DISTRIBUTOR_MISMATCH", "Distributor does not match original invoice", 422)
    requested = {}
    for row in payload["items"]:
        item_code = row["item_code"]
        requested[item_code] = requested.get(item_code, Decimal("0")) + Decimal(str(row["qty"]))
    original_qty = {}
    original_rates = {}
    for row in original.items:
        original_qty[row.item_code] = original_qty.get(row.item_code, Decimal("0")) + Decimal(str(row.qty))
        original_rates.setdefault(row.item_code, Decimal(str(row.rate)))
    return_parents = set(
        frappe.get_all(
            "Sales Invoice",
            filters={"return_against": original.name, "docstatus": ["in", [0, 1]], "is_return": 1},
            pluck="name",
        )
    )
    submitted_returns = []
    if return_parents:
        submitted_returns = frappe.get_all(
            "Sales Invoice Item",
            filters={
                "parenttype": "Sales Invoice",
                "parent": ["in", list(return_parents)],
                "item_code": ["in", list(requested)],
            },
            fields=["item_code", "qty", "parent"],
        )
    returned = {}
    for row in submitted_returns:
        if row.parent in return_parents:
            returned.setdefault(row.item_code, []).append(row.qty)
    for item_code, qty in requested.items():
        remaining = remaining_return_quantity(original_qty.get(item_code, 0), returned.get(item_code, []))
        if item_code not in original_qty or qty > remaining:
            raise IntegrationError("RETURN_QTY_EXCEEDED", f"Return quantity exceeds invoice quantity for {item_code}", 422)
    for row in payload["items"]:
        item_code = row["item_code"]
        if item_code in original_rates and abs(Decimal(str(row["rate"])) - original_rates[item_code]) > Decimal("0.02"):
            raise IntegrationError("RETURN_RATE_MISMATCH", f"Return rate differs from the original invoice for {item_code}", 422, field="rate")
    credit = make_sales_return(original.name)
    selected_rows = []
    seen_items = set()
    for row in credit.items:
        if row.item_code in requested and row.item_code not in seen_items:
            selected_rows.append(row)
            seen_items.add(row.item_code)
    credit.items = selected_rows
    for row in credit.items:
        row.qty = -requested[row.item_code]
        row.rate = original_rates[row.item_code]
    credit.update_stock = 0
    credit.custom_bs_credit_note_id = payload["bs_credit_note_id"]
    credit.custom_bs_order_id = payload["bs_order_id"]
    credit.custom_bs_source_datetime = payload["credit_note_datetime"]
    credit.custom_bs_request_fingerprint = fingerprint
    credit.remarks = f"{payload['reason_code']}: {payload['reason']}"
    try:
        credit.insert(ignore_permissions=True)
    except frappe.UniqueValidationError:
        winner = frappe.db.get_value("Sales Invoice", {"custom_bs_credit_note_id":payload["bs_credit_note_id"]}, ["name", "custom_bs_request_fingerprint"], as_dict=True)
        if winner and winner.custom_bs_request_fingerprint == fingerprint:
            return {"credit_note": winner.name, "duplicate": True}, False
        raise ConflictError("CREDIT_NOTE_ID_CONFLICT", "The BS credit-note ID already exists with different data", field="bs_credit_note_id")
    settings = frappe.get_cached_doc("BS Integration Settings")
    tolerance = Decimal(str(settings.amount_tolerance or "0.02"))
    if abs(abs(Decimal(str(credit.grand_total))) - Decimal(str(payload["grand_total"]))) > tolerance:
        raise IntegrationError("ERP_CREDIT_TOTAL_MISMATCH", "ERPNext calculated credit total does not match BS grand total", 422)
    if settings.auto_submit_credit_note:
        credit.submit()
    return {"credit_note": credit.name, "duplicate": False, "docstatus": credit.docstatus}, True
