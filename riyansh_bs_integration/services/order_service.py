from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation

from riyansh_bs_integration.core.errors import ConflictError, IntegrationError
from riyansh_bs_integration.core.validation import (
    require,
    request_fingerprint,
    to_database_datetime,
    validate_mobile,
    validate_pincode,
)


def _money(value, field):
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"))
        if not amount.is_finite():
            raise InvalidOperation
        return amount
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise IntegrationError("INVALID_AMOUNT", f"{field} is invalid", 422, field=field) from exc


def validate_order_payload(payload):
    """Validate the agreed BS API 3 contract."""
    if not isinstance(payload, dict):
        raise IntegrationError(
            "INVALID_JSON", "Request body must be a JSON object", 400
        )

    payload = dict(payload)

    for field in (
        "bs_order_id", "customer_id", "order_datetime",
        "warehouse_code", "shipping_address", "items"
    ):
        require(payload, field)

    for field in ("bs_order_id", "customer_id", "warehouse_code"):
        value = payload[field]
        if not isinstance(value, str) or not value.strip():
            raise IntegrationError(
                "INVALID_FIELD",
                f"{field} must be a nonempty string",
                422,
                field=field
            )
        payload[field] = value.strip()

    try:
        dt = datetime.fromisoformat(str(payload["order_datetime"]))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError
    except (ValueError, TypeError, OverflowError) as exc:
        raise IntegrationError(
            "INVALID_DATETIME",
            "order_datetime must include a valid timezone",
            422,
            field="order_datetime"
        ) from exc

    payload["order_datetime"] = dt.isoformat()
    payload["delivery_date"] = dt.date().isoformat()

    address = payload["shipping_address"]

    if not isinstance(address, dict):
        raise IntegrationError(
            "INVALID_ADDRESS",
            "shipping_address must be an object",
            422,
            field="shipping_address"
        )

    address = dict(address)

    for field in (
        "name", "mobile", "address_line_1",
        "city", "state", "pincode", "country"
    ):
        value = require(address, field)
        if not isinstance(value, str) or not value.strip():
            raise IntegrationError(
                "INVALID_ADDRESS_FIELD",
                f"shipping_address.{field} is required",
                422,
                field=f"shipping_address.{field}"
            )
        address[field] = value.strip()

    address["mobile"] = validate_mobile(address["mobile"])
    address["pincode"] = validate_pincode(address["pincode"])

    payload["shipping_address"] = address

    if not isinstance(payload["items"], list) or not payload["items"]:
        raise IntegrationError(
            "EMPTY_ITEMS",
            "At least one item is required",
            422,
            field="items"
        )

    normalized_items = []

    for index, item in enumerate(payload["items"]):
        if not isinstance(item, dict):
            raise IntegrationError(
                "INVALID_ITEM",
                "Each item must be an object",
                422,
                field=f"items[{index}]"
            )

        item_code = require(item, "item_code")

        if not isinstance(item_code, str) or not item_code.strip():
            raise IntegrationError(
                "INVALID_ITEM_CODE",
                "item_code must be a nonempty string",
                422,
                field=f"items[{index}].item_code"
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
                "Item quantity or rate is invalid",
                422,
                field=f"items[{index}]"
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
                field=f"items[{index}]"
            )

        uom = item.get("uom")

        if uom is not None and (
            not isinstance(uom, str) or not uom.strip()
        ):
            raise IntegrationError(
                "INVALID_UOM",
                "uom must be a nonempty string when provided",
                422,
                field=f"items[{index}].uom"
            )

        normalized_items.append({
            "item_code": item_code.strip(),
            "qty": str(qty),
            "rate": str(rate),
            "uom": uom.strip() if uom else None
        })

    payload["items"] = normalized_items

    # Financial values are calculated by ERPNext.
    # Do not accept BS-supplied totals as authoritative.
    for field in (
        "taxable_value", "total_tax", "grand_total",
        "shipping_amount", "discount_amount",
        "payment_reference"
    ):
        payload.pop(field, None)

    payload["currency"] = "INR"
    payload["payment_status"] = "PAID"

    return payload


from riyansh_bs_integration.services.gst_service import set_document_gst

def create_sales_order(payload, correlation_id):
    import frappe
    from frappe.utils import get_system_timezone
    payload = validate_order_payload(payload)
    fingerprint = request_fingerprint(payload)
    existing = frappe.db.get_value("Sales Order", {"custom_bs_order_id": payload["bs_order_id"]}, ["name", "custom_bs_request_fingerprint"], as_dict=True)
    if existing:
        if existing.custom_bs_request_fingerprint != fingerprint:
            raise ConflictError("ORDER_ID_CONFLICT", "The BS order ID already exists with different data", field="bs_order_id")
        return {"sales_order": existing.name, "duplicate": True}, False
    customer = frappe.db.get_value(
        "Customer",
        {"custom_distributor_id": payload["customer_id"]},
        "name",
    )
    if not customer:
        raise IntegrationError(
            "CUSTOMER_NOT_FOUND",
            "Customer ID does not exist in ERPNext",
            422,
            field="customer_id",
        )
    settings = frappe.get_cached_doc("BS Integration Settings")
    _validate_order_configuration(frappe, settings, payload)
    warehouse = _resolve_warehouse(frappe, settings, payload["warehouse_code"])
    doc = frappe.new_doc("Sales Order")
    doc.company, doc.customer = settings.company, customer
    doc.currency = "INR"
    doc.selling_price_list = settings.default_price_list
    local_order_datetime = to_database_datetime(
        payload["order_datetime"],
        get_system_timezone(),
        "order_datetime",
    )
    payload["delivery_date"] = local_order_datetime.date().isoformat()
    doc.transaction_date = local_order_datetime.date()
    doc.delivery_date = payload["delivery_date"]
    doc.custom_bs_order_id = payload["bs_order_id"]
    doc.custom_bs_payment_reference = None
    doc.custom_bs_source_datetime = to_database_datetime(
        payload["order_datetime"], get_system_timezone(), "order_datetime"
    )
    doc.custom_bs_request_fingerprint = fingerprint
    shipping_address = _get_or_create_shipping_address(frappe, customer, payload)
    doc.shipping_address_name = shipping_address
    doc.customer_address = shipping_address
    for item in payload["items"]:
        item_record = frappe.db.get_value("Item", item["item_code"], ["name", "disabled", "stock_uom", "is_stock_item"], as_dict=True)
        if not item_record:
            raise IntegrationError("ITEM_NOT_FOUND", f"Item {item['item_code']} does not exist", 422, field="item_code")
        if item_record.disabled:
            raise IntegrationError("ITEM_DISABLED", f"Item {item['item_code']} is disabled", 422, field="item_code")
        item["uom"] = item.get("uom") or item_record.stock_uom
        if not frappe.db.exists("UOM Conversion Detail", {"parent": item["item_code"], "uom": item["uom"]}) and item["uom"] != item_record.stock_uom:
            raise IntegrationError("INVALID_UOM", f"UOM {item['uom']} is not valid for {item['item_code']}", 422, field="uom")
        # BS supplies the confirmed transaction rate.
        # Draft orders do not require available stock.
        # Stock availability is checked during fulfillment.
        doc.append("items", {"item_code":item["item_code"], "qty":item["qty"], "uom":item["uom"], "rate":item["rate"], "discount_amount":item.get("discount_amount", 0), "warehouse":warehouse, "delivery_date":payload["delivery_date"]})

    doc.ignore_pricing_rule = 1
    doc.set_missing_values()

    set_document_gst(
        frappe,
        doc,
        settings.company,
        customer,
        shipping_address,
        doc.transaction_date,
    )
    try:
        doc.insert(ignore_permissions=True)
    except (
            frappe.UniqueValidationError,
            frappe.DuplicateEntryError,
        ):
            # Roll back the losing insert transaction before reading
            # the committed document created by another request.
            frappe.db.rollback()

            winner = frappe.db.get_value(
                "Sales Order",
                {"custom_bs_order_id": payload["bs_order_id"]},
                ["name", "custom_bs_request_fingerprint"],
                as_dict=True,
            )

            if not winner:
                # The unique violation may concern another field.
                # Preserve the original database exception.
                raise

            if winner.custom_bs_request_fingerprint != fingerprint:
                raise ConflictError(
                    "ORDER_ID_CONFLICT",
                    "The BS order ID already exists with different data",
                    field="bs_order_id",
                )

            return {
                "sales_order": winner.name,
                "duplicate": True,
            }, False
    # UAT safety: never auto-submit an API-created Sales Order.
    # Submission requires a separately reviewed release.
    if doc.docstatus != 0:
        raise IntegrationError(
            "UNEXPECTED_DOCSTATUS",
            "Sales Order must remain Draft",
            500,
        )
    return {"sales_order": doc.name, "duplicate": False, "docstatus": doc.docstatus}, True


def _validate_order_configuration(frappe, settings, payload):
    required = {
        "company": "Company",
        "default_price_list": "Default Price List",
    }
    for fieldname, label in required.items():
        if not getattr(settings, fieldname, None):
            raise IntegrationError(
                "INTEGRATION_NOT_CONFIGURED",
                f"{label} is not configured in BS Integration Settings",
                503,
                field=fieldname,
            )
    if not frappe.db.exists("Company", settings.company):
        raise IntegrationError("COMPANY_NOT_FOUND", "Configured company does not exist", 503)
    if not frappe.db.exists("Price List", settings.default_price_list):
        raise IntegrationError("PRICE_LIST_NOT_FOUND", "Configured price list does not exist", 503)
    if not frappe.db.exists("Currency", payload["currency"]):
        raise IntegrationError("CURRENCY_NOT_FOUND", "Currency does not exist", 422, field="currency")
    template = getattr(settings, "default_sales_taxes_and_charges_template", None)
    if template and not frappe.db.exists("Sales Taxes and Charges Template", template):
        raise IntegrationError(
            "TAX_TEMPLATE_NOT_FOUND",
            "Configured sales tax template does not exist",
            503,
        )


def _append_shipping_charge(doc, frappe, settings, shipping_amount):
    amount = _money(shipping_amount or 0, "shipping_amount")
    if amount == 0:
        return
    account = getattr(settings, "shipping_charge_account", None)
    if not account:
        raise IntegrationError(
            "SHIPPING_ACCOUNT_NOT_CONFIGURED",
            "Shipping Charge Account is required for orders with shipping amount",
            503,
            field="shipping_amount",
        )
    account_row = frappe.db.get_value(
        "Account",
        account,
        ["name", "company", "is_group", "disabled"],
        as_dict=True,
    )
    if not account_row or account_row.is_group or account_row.disabled:
        raise IntegrationError(
            "INVALID_SHIPPING_ACCOUNT",
            "Configured shipping charge account is unavailable",
            503,
        )
    if account_row.company != settings.company:
        raise IntegrationError(
            "INVALID_SHIPPING_ACCOUNT",
            "Shipping charge account does not belong to the configured company",
            503,
        )
    doc.append(
        "taxes",
        {
            "charge_type": "Actual",
            "account_head": account,
            "description": "Shipping Charges",
            "tax_amount": float(amount),
        },
    )


def _resolve_warehouse(frappe, settings, code):
    try:
        mapping = json.loads(settings.warehouse_mapping_json or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise IntegrationError(
            "INVALID_WAREHOUSE_MAPPING",
            "Warehouse Mapping JSON is not valid JSON",
            422,
            field="warehouse_code",
        ) from exc
    if not isinstance(mapping, dict):
        raise IntegrationError(
            "INVALID_WAREHOUSE_MAPPING",
            "Warehouse Mapping JSON must be an object",
            422,
            field="warehouse_code",
        )
    warehouse = mapping.get(code)
    warehouse_row = None
    if warehouse:
        warehouse_row = frappe.db.get_value(
            "Warehouse",
            warehouse,
            ["name", "company", "is_group", "disabled"],
            as_dict=True,
        )
    if not warehouse_row:
        raise IntegrationError("WAREHOUSE_NOT_MAPPED", f"Warehouse code {code} is not mapped", 422, field="warehouse_code")
    if warehouse_row.is_group or warehouse_row.disabled:
        raise IntegrationError("WAREHOUSE_UNAVAILABLE", f"Warehouse {warehouse} is unavailable", 422, field="warehouse_code")
    if warehouse_row.company and warehouse_row.company != settings.company:
        raise IntegrationError("WAREHOUSE_COMPANY_MISMATCH", f"Warehouse {warehouse} belongs to another company", 422, field="warehouse_code")
    return warehouse


def _validate_price(frappe, settings, item):
    prices = frappe.get_all(
        "Item Price",
        filters={"item_code": item["item_code"], "price_list": settings.default_price_list, "selling": 1},
        fields=["price_list_rate"],
        order_by="valid_from desc",
        limit_page_length=1,
    )
    if not prices:
        raise IntegrationError("PRICE_NOT_FOUND", f"Selling price is not configured for {item['item_code']}", 422, field="rate")
    # ERP reference price must exist. BS supplies the confirmed transaction rate.
    return


def _get_or_create_shipping_address(frappe, customer, payload):
    """Never reuse a BS shipping address belonging to another customer."""
    address_title = f"BS-{payload['bs_order_id']}"
    supplied = payload["shipping_address"]

    existing = frappe.db.get_value(
        "Address",
        {"address_title": address_title},
        "name",
    )

    if existing:
        linked = frappe.db.exists(
            "Dynamic Link",
            {
                "parenttype": "Address",
                "parent": existing,
                "link_doctype": "Customer",
                "link_name": customer,
            },
        )

        if not linked:
            raise IntegrationError(
                "ADDRESS_CUSTOMER_MISMATCH",
                "Existing BS address belongs to another customer",
                409,
                field="shipping_address",
            )

        saved = frappe.db.get_value(
            "Address",
            existing,
            [
                "address_line1",
                "city",
                "state",
                "pincode",
                "country",
                "phone",
            ],
            as_dict=True,
        )

        expected = {
            "address_line1": supplied["address_line_1"],
            "city": supplied["city"],
            "state": supplied["state"],
            "pincode": supplied["pincode"],
            "country": supplied["country"],
            "phone": supplied["mobile"],
        }

        if not saved or any(
            str(saved.get(key) or "").strip() != str(value).strip()
            for key, value in expected.items()
        ):
            raise IntegrationError(
                "ADDRESS_DETAILS_CONFLICT",
                "Existing BS address differs from supplied address",
                409,
                field="shipping_address",
            )

        return existing

    address = frappe.get_doc({
        "doctype": "Address",
        "address_title": address_title,
        "address_type": "Shipping",
        "address_line1": supplied["address_line_1"],
        "address_line2": supplied.get("address_line_2"),
        "city": supplied["city"],
        "state": supplied["state"],
        "pincode": supplied["pincode"],
        "country": supplied["country"],
        "phone": supplied["mobile"],
        "is_shipping_address": 1,
        "links": [{
            "link_doctype": "Customer",
            "link_name": customer,
        }],
    })

    address.insert(ignore_permissions=True)
    return address.name
