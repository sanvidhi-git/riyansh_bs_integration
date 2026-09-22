from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from riyansh_bs_integration.core.errors import ConflictError, IntegrationError
from riyansh_bs_integration.core.validation import require, request_fingerprint, validate_mobile, validate_pincode


def _money(value, field):
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError) as exc:
        raise IntegrationError("INVALID_AMOUNT", f"{field} is invalid", 422, field=field) from exc


def validate_order_payload(payload):
    if not isinstance(payload, dict):
        raise IntegrationError("INVALID_JSON", "Request body must be a JSON object", 400)
    for field in ("bs_order_id", "distributor_id", "order_datetime", "warehouse_code", "currency", "payment_status", "shipping_address", "items"):
        require(payload, field)
    try:
        dt = datetime.fromisoformat(str(payload["order_datetime"]))
        if dt.tzinfo is None:
            raise ValueError
    except ValueError as exc:
        raise IntegrationError("INVALID_DATETIME", "order_datetime must include timezone", 422, field="order_datetime") from exc
    if not isinstance(payload["items"], list) or not payload["items"]:
        raise IntegrationError("EMPTY_ITEMS", "At least one item is required", 422, field="items")
    address = payload["shipping_address"]
    if not isinstance(address, dict):
        raise IntegrationError("INVALID_ADDRESS", "shipping_address must be an object", 422, field="shipping_address")
    for field in ("name", "address_line_1", "city", "state"):
        require(address, field)
    validate_mobile(require(address, "mobile")); validate_pincode(require(address, "pincode"))
    line_taxable = line_tax = line_total = Decimal("0")
    for index, item in enumerate(payload["items"]):
        require(item, "item_code"); require(item, "uom")
        qty, rate = _money(require(item, "qty"), f"items[{index}].qty"), _money(require(item, "rate"), f"items[{index}].rate")
        if qty <= 0 or rate < 0:
            raise IntegrationError("INVALID_ITEM_AMOUNT", "Quantity must be positive and rate non-negative", 422, field=f"items[{index}]")
        discount = _money(item.get("discount_amount", 0), f"items[{index}].discount_amount")
        taxable = _money(require(item, "taxable_amount"), f"items[{index}].taxable_amount")
        tax = _money(require(item, "tax_amount"), f"items[{index}].tax_amount")
        total = _money(require(item, "line_total"), f"items[{index}].line_total")
        if abs(taxable - ((qty * rate) - discount)) > Decimal("0.02"):
            raise IntegrationError("LINE_TAXABLE_MISMATCH", "Item taxable amount does not match quantity, rate and discount", 422, field=f"items[{index}].taxable_amount")
        if abs(total - (taxable + tax)) > Decimal("0.02"):
            raise IntegrationError("LINE_TOTAL_MISMATCH", "Item line total does not reconcile", 422, field=f"items[{index}].line_total")
        line_taxable += taxable; line_tax += tax; line_total += total
    taxable_value, total_tax, shipping = _money(require(payload, "taxable_value"), "taxable_value"), _money(require(payload, "total_tax"), "total_tax"), _money(payload.get("shipping_amount", 0), "shipping_amount")
    grand_total = _money(require(payload, "grand_total"), "grand_total")
    if abs(line_taxable - taxable_value) > Decimal("0.02") or abs(line_tax - total_tax) > Decimal("0.02") or abs(grand_total - (taxable_value + total_tax + shipping)) > Decimal("0.02"):
        raise IntegrationError("ORDER_TOTAL_MISMATCH", "Order totals do not reconcile", 422)
    if str(payload["payment_status"]).upper() == "PAID" and not payload.get("payment_reference"):
        raise IntegrationError("MISSING_PAYMENT_REFERENCE", "Paid order requires payment_reference", 422, field="payment_reference")
    return payload


def create_sales_order(payload, correlation_id):
    import frappe
    from erpnext.accounts.services.taxes import TaxService
    payload = validate_order_payload(payload)
    fingerprint = request_fingerprint(payload)
    existing = frappe.db.get_value("Sales Order", {"custom_bs_order_id": payload["bs_order_id"]}, ["name", "custom_bs_request_fingerprint"], as_dict=True)
    if existing:
        if existing.custom_bs_request_fingerprint != fingerprint:
            raise ConflictError("ORDER_ID_CONFLICT", "The BS order ID already exists with different data", field="bs_order_id")
        return {"sales_order": existing.name, "duplicate": True}, False
    onboarding = frappe.db.get_value("BS Distributor Onboarding", {"distributor_id":payload["distributor_id"], "kyc_status":"Passed"}, ["customer"], as_dict=True)
    if not onboarding or not onboarding.customer:
        raise IntegrationError("DISTRIBUTOR_NOT_VERIFIED", "Distributor is not KYC verified", 422, field="distributor_id")
    settings = frappe.get_cached_doc("BS Integration Settings")
    warehouse = _resolve_warehouse(frappe, settings, payload["warehouse_code"])
    doc = frappe.new_doc("Sales Order")
    doc.company, doc.customer = settings.company, onboarding.customer
    doc.currency = payload["currency"]
    doc.selling_price_list = settings.default_price_list
    doc.transaction_date = datetime.fromisoformat(payload["order_datetime"]).date()
    doc.custom_bs_order_id = payload["bs_order_id"]
    doc.custom_bs_payment_reference = payload.get("payment_reference")
    doc.custom_bs_source_datetime = payload["order_datetime"]
    doc.custom_bs_request_fingerprint = fingerprint
    doc.shipping_address_name = _get_or_create_shipping_address(frappe, onboarding.customer, payload)
    for item in payload["items"]:
        item_record = frappe.db.get_value("Item", item["item_code"], ["name", "disabled", "stock_uom", "is_stock_item"], as_dict=True)
        if not item_record:
            raise IntegrationError("ITEM_NOT_FOUND", f"Item {item['item_code']} does not exist", 422, field="item_code")
        if item_record.disabled:
            raise IntegrationError("ITEM_DISABLED", f"Item {item['item_code']} is disabled", 422, field="item_code")
        if not frappe.db.exists("UOM Conversion Detail", {"parent": item["item_code"], "uom": item["uom"]}) and item["uom"] != item_record.stock_uom:
            raise IntegrationError("INVALID_UOM", f"UOM {item['uom']} is not valid for {item['item_code']}", 422, field="uom")
        _validate_price(frappe, settings, item)
        if item_record.is_stock_item:
            available = Decimal(str(frappe.db.get_value("Bin", {"item_code": item["item_code"], "warehouse": warehouse}, "actual_qty") or 0))
            if available < Decimal(str(item["qty"])):
                raise IntegrationError("INSUFFICIENT_STOCK", f"Insufficient stock for {item['item_code']}", 422, field="qty")
        doc.append("items", {"item_code":item["item_code"], "qty":item["qty"], "uom":item["uom"], "rate":item["rate"], "discount_amount":item.get("discount_amount", 0), "warehouse":warehouse})
    if settings.default_sales_taxes_and_charges_template:
        doc.taxes_and_charges = settings.default_sales_taxes_and_charges_template
        TaxService(doc).set_taxes()
    try:
        doc.insert(ignore_permissions=True)
    except frappe.UniqueValidationError:
        winner = frappe.db.get_value("Sales Order", {"custom_bs_order_id": payload["bs_order_id"]}, ["name", "custom_bs_request_fingerprint"], as_dict=True)
        if winner and winner.custom_bs_request_fingerprint == fingerprint:
            return {"sales_order": winner.name, "duplicate": True}, False
        raise ConflictError("ORDER_ID_CONFLICT", "The BS order ID already exists with different data", field="bs_order_id")
    tolerance = Decimal(str(settings.amount_tolerance or "0.02"))
    if abs(Decimal(str(doc.grand_total)) - Decimal(str(payload["grand_total"]))) > tolerance:
        raise IntegrationError("ERP_TOTAL_MISMATCH", "ERPNext calculated total does not match BS grand total", 422)
    if settings.auto_submit_sales_order:
        doc.submit()
    return {"sales_order": doc.name, "duplicate": False, "docstatus": doc.docstatus}, True


def _resolve_warehouse(frappe, settings, code):
    import json
    mapping = json.loads(settings.warehouse_mapping_json or "{}")
    warehouse = mapping.get(code)
    if not warehouse or not frappe.db.exists("Warehouse", warehouse):
        raise IntegrationError("WAREHOUSE_NOT_MAPPED", f"Warehouse code {code} is not mapped", 422, field="warehouse_code")
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
    expected = prices[0].price_list_rate
    expected, supplied = Decimal(str(expected)), Decimal(str(item["rate"]))
    allowed = abs(expected) * Decimal(str(settings.price_tolerance_percent or 0)) / Decimal("100")
    if abs(expected - supplied) > allowed:
        raise IntegrationError("STALE_PRICE", f"Rate does not match ERPNext price for {item['item_code']}", 422, field="rate")


def _get_or_create_shipping_address(frappe, customer, payload):
    address_name = f"BS-{payload['bs_order_id']}"
    existing = frappe.db.get_value("Address", {"address_title": address_name}, "name")
    if existing:
        return existing
    address = payload["shipping_address"]
    return frappe.get_doc({
        "doctype": "Address",
        "address_title": address_name,
        "address_type": "Shipping",
        "address_line1": address["address_line_1"],
        "address_line2": address.get("address_line_2"),
        "city": address["city"],
        "state": address["state"],
        "pincode": address["pincode"],
        "country": address.get("country") or "India",
        "phone": address["mobile"],
        "is_shipping_address": 1,
        "links": [{"link_doctype": "Customer", "link_name": customer}],
    }).insert(ignore_permissions=True).name
