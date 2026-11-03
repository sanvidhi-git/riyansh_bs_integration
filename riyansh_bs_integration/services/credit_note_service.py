from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from riyansh_bs_integration.core.errors import ConflictError, IntegrationError
from riyansh_bs_integration.core.validation import require, request_fingerprint, to_database_datetime


def _number(value, field):
    try:
        number = Decimal(str(value))
        if not number.is_finite():
            raise InvalidOperation
        return number
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise IntegrationError("INVALID_AMOUNT", f"{field} is invalid", 422, field=field) from exc


def remaining_return_quantity(original_qty, returned_quantities):
    remaining = Decimal(str(original_qty)) - sum(
        (abs(Decimal(str(qty))) for qty in returned_quantities), Decimal("0")
    )
    return max(remaining, Decimal("0"))


def validate_credit_note_payload(payload):
    """Validate the agreed minimal BS API 4 contract."""
    if not isinstance(payload, dict):
        raise IntegrationError(
            "INVALID_JSON",
            "Request body must be a JSON object",
            400,
        )

    payload = dict(payload)

    for field in (
        "bs_credit_note_id",
        "bs_order_id",
        "customer_id",
        "credit_note_datetime",
        "warehouse_code",
        "items",
    ):
        require(payload, field)

    for field in (
        "bs_credit_note_id",
        "bs_order_id",
        "customer_id",
        "warehouse_code",
    ):
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise IntegrationError(
                "INVALID_FIELD",
                f"{field} must be a nonempty string",
                422,
                field=field,
            )
        payload[field] = value.strip()

    status = payload.get("status", "APPROVED")
    if status is None or str(status).strip().upper() != "APPROVED":
        raise IntegrationError(
            "RETURN_NOT_APPROVED",
            "Only APPROVED credit notes are accepted",
            422,
            field="status",
        )
    payload["status"] = "APPROVED"

    reason = payload.get("reason")
    if reason is None or reason == "":
        reason = "Customer return approved"
    if not isinstance(reason, str) or not reason.strip():
        raise IntegrationError(
            "INVALID_REASON",
            "reason must be a nonempty string",
            422,
            field="reason",
        )
    payload["reason"] = reason.strip()
    payload["reason_code"] = "CUSTOMER_RETURN"

    try:
        dt = datetime.fromisoformat(
            str(payload["credit_note_datetime"])
        )
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError, OverflowError) as exc:
        raise IntegrationError(
            "INVALID_DATETIME",
            "credit_note_datetime must include a valid timezone",
            422,
            field="credit_note_datetime",
        ) from exc

    payload["credit_note_datetime"] = dt.isoformat()

    if not isinstance(payload["items"], list) or not payload["items"]:
        raise IntegrationError(
            "EMPTY_ITEMS",
            "At least one credit-note item is required",
            422,
            field="items",
        )

    normalized = []
    for index, item in enumerate(payload["items"]):
        if not isinstance(item, dict):
            raise IntegrationError(
                "INVALID_ITEM",
                "Each item must be an object",
                422,
                field=f"items[{index}]",
            )

        code = require(item, "item_code")
        if not isinstance(code, str) or not code.strip():
            raise IntegrationError(
                "INVALID_ITEM_CODE",
                "item_code must be a nonempty string",
                422,
                field=f"items[{index}].item_code",
            )

        try:
            raw_qty = require(item, "qty")
            raw_rate = require(item, "rate")
            if isinstance(raw_qty, bool) or isinstance(raw_rate, bool):
                raise InvalidOperation

            qty = Decimal(str(raw_qty))
            rate = Decimal(str(raw_rate))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise IntegrationError(
                "INVALID_ITEM_AMOUNT",
                "Quantity or rate is invalid",
                422,
                field=f"items[{index}]",
            ) from exc

        if (
            not qty.is_finite()
            or not rate.is_finite()
            or qty <= 0
            or rate < 0
        ):
            raise IntegrationError(
                "INVALID_ITEM_AMOUNT",
                "Quantity must be positive and rate nonnegative",
                422,
                field=f"items[{index}]",
            )

        normalized.append({
            "item_code": code.strip(),
            "qty": str(qty),
            "rate": str(rate),
        })

    payload["items"] = normalized

    # These old BS fields are no longer authoritative.
    for field in (
        "original_invoice_reference",
        "taxable_value",
        "total_tax",
        "grand_total",
    ):
        payload.pop(field, None)

    return payload


def create_credit_note(payload, correlation_id):
    """Create a standalone draft return against a verified BS order."""
    import frappe
    from frappe.utils import get_system_timezone

    from riyansh_bs_integration.services.gst_service import (
        set_document_gst,
        get_customer_address,
    )
    from riyansh_bs_integration.services.order_service import (
        _resolve_warehouse,
    )

    payload = validate_credit_note_payload(payload)
    fingerprint = request_fingerprint(payload)

    existing = frappe.db.get_value(
        "Sales Invoice",
        {"custom_bs_credit_note_id": payload["bs_credit_note_id"]},
        ["name", "custom_bs_request_fingerprint"],
        as_dict=True,
    )
    if existing:
        if existing.custom_bs_request_fingerprint != fingerprint:
            raise ConflictError(
                "CREDIT_NOTE_ID_CONFLICT",
                "Credit-note ID already exists with different data",
                field="bs_credit_note_id",
            )
        return {"credit_note": existing.name, "duplicate": True}, False

    settings = frappe.get_cached_doc("BS Integration Settings")

    customer = frappe.db.get_value(
        "Customer",
        {"custom_distributor_id": payload["customer_id"]},
        "name",
    )
    if not customer:
        raise IntegrationError(
            "CUSTOMER_NOT_FOUND",
            "Customer ID does not exist",
            422,
            field="customer_id",
        )

    order_name = frappe.db.get_value(
        "Sales Order",
        {"custom_bs_order_id": payload["bs_order_id"]},
        "name",
    )
    if not order_name:
        raise IntegrationError(
            "ORDER_NOT_FOUND",
            "Original BS Sales Order is not available in ERPNext",
            422,
            field="bs_order_id",
        )

    # Serialize different returns consuming quantities from one BS order.
    frappe.db.sql(
        "SELECT name FROM `tabSales Order` WHERE name = %s FOR UPDATE",
        (order_name,),
    )

    original = frappe.get_doc("Sales Order", order_name)

    if original.docstatus == 2:
        raise IntegrationError(
            "ORDER_CANCELLED",
            "Cannot return against a cancelled order",
            422,
        )

    if original.customer != customer or original.company != settings.company:
        raise IntegrationError(
            "ORDER_CUSTOMER_MISMATCH",
            "Order customer or company does not match",
            422,
        )

    warehouse = _resolve_warehouse(
        frappe, settings, payload["warehouse_code"]
    )

    available = {}
    original_rates = {}
    original_rows = {}

    for row in original.items:
        if row.warehouse != warehouse:
            continue

        code = row.item_code
        available[code] = available.get(code, Decimal("0")) + Decimal(
            str(row.qty)
        )

        rate = Decimal(str(row.rate))
        if code in original_rates and original_rates[code] != rate:
            raise IntegrationError(
                "AMBIGUOUS_ORDER_RATE",
                f"Original order contains multiple rates for {code}",
                422,
            )

        original_rates[code] = rate
        original_rows[code] = row

    requested = {}
    for row in payload["items"]:
        code = row["item_code"]
        requested[code] = requested.get(code, Decimal("0")) + Decimal(
            str(row["qty"])
        )

        if code not in available:
            raise IntegrationError(
                "RETURN_ITEM_NOT_ORDERED",
                f"Item {code} is not in this order and warehouse",
                422,
            )

        if abs(
            Decimal(str(row["rate"])) - original_rates[code]
        ) > Decimal("0.02"):
            raise IntegrationError(
                "RETURN_RATE_MISMATCH",
                f"Return rate differs from original order for {code}",
                422,
                field="rate",
            )

    previous_rows = frappe.db.sql(
        """
        SELECT sii.item_code, sii.qty
        FROM `tabSales Invoice Item` sii
        JOIN `tabSales Invoice` si ON si.name = sii.parent
        WHERE si.custom_bs_order_id = %s
          AND si.is_return = 1
          AND si.docstatus IN (0, 1)
          AND sii.warehouse = %s
        """,
        (payload["bs_order_id"], warehouse),
        as_dict=True,
    )

    already_returned = {}
    for row in previous_rows:
        code = row.item_code
        already_returned[code] = (
            already_returned.get(code, Decimal("0"))
            + abs(Decimal(str(row.qty)))
        )

    for code, qty in requested.items():
        remaining = available[code] - already_returned.get(
            code, Decimal("0")
        )
        if qty > remaining:
            raise IntegrationError(
                "RETURN_QTY_EXCEEDED",
                f"Return exceeds remaining order quantity for {code}",
                422,
            )

    address_name = (
        original.shipping_address_name
        or original.customer_address
    )
    if not address_name:
        raise IntegrationError(
            "ORDER_ADDRESS_MISSING",
            "Original order has no verified customer address",
            422,
        )

    get_customer_address(frappe, customer, address_name)

    dt = to_database_datetime(
        payload["credit_note_datetime"],
        get_system_timezone(),
        "credit_note_datetime",
    )

    credit = frappe.new_doc("Sales Invoice")
    credit.company = settings.company
    credit.customer = customer
    credit.is_return = 1
    credit.return_against = None
    credit.update_stock = 0
    credit.currency = "INR"
    credit.selling_price_list = settings.default_price_list
    credit.posting_date = dt.date().isoformat()
    credit.set_posting_time = 1
    credit.posting_time = dt.strftime("%H:%M:%S")
    credit.due_date = dt.date().isoformat()
    credit.customer_address = address_name
    credit.shipping_address_name = address_name
    credit.ignore_pricing_rule = 1

    credit.custom_bs_credit_note_id = payload["bs_credit_note_id"]
    credit.custom_bs_order_id = payload["bs_order_id"]
    credit.custom_bs_source_datetime = to_database_datetime(
        payload["credit_note_datetime"],
        get_system_timezone(),
        "credit_note_datetime",
    )
    credit.custom_bs_request_fingerprint = fingerprint
    credit.remarks = payload["reason"]

    for row in payload["items"]:
        source = original_rows[row["item_code"]]

        credit.append("items", {
            "item_code": row["item_code"],
            "qty": -float(Decimal(str(row["qty"]))),
            "rate": float(Decimal(str(row["rate"]))),
            "uom": source.uom,
            "conversion_factor": source.conversion_factor or 1,
            "warehouse": warehouse,
        })

    credit.set_missing_values()

    set_document_gst(
        frappe,
        credit,
        settings.company,
        customer,
        address_name,
        credit.posting_date,
    )

    try:
        credit.insert(ignore_permissions=True)
    except (
            frappe.UniqueValidationError,
            frappe.DuplicateEntryError,
        ):
            # Roll back the losing insert transaction before reading
            # the committed document created by another request.
            frappe.db.rollback()

            winner = frappe.db.get_value(
                "Sales Invoice",
                {"custom_bs_credit_note_id": payload["bs_credit_note_id"]},
                ["name", "custom_bs_request_fingerprint"],
                as_dict=True,
            )

            if not winner:
                # The unique violation may concern another field.
                # Preserve the original database exception.
                raise

            if winner.custom_bs_request_fingerprint != fingerprint:
                raise ConflictError(
                    "CREDIT_NOTE_ID_CONFLICT",
                    "Credit-note ID already exists with different data",
                    field="bs_credit_note_id",
                )

            return {
                "credit_note": winner.name,
                "duplicate": True,
            }, False

    if credit.docstatus != 0 or credit.update_stock:
        raise IntegrationError(
            "CREDIT_NOTE_NOT_DRAFT",
            "Credit Note must remain Draft without stock posting",
            500,
        )

    return {
        "credit_note": credit.name,
        "duplicate": False,
        "docstatus": credit.docstatus,
    }, True
