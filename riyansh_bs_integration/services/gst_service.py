"""GST resolution for BS Sales Orders and standalone Credit Notes.

Rates are derived from ERPNext Item Tax Templates, not from BS.
Unsupported or ambiguous configurations fail closed.
"""

from __future__ import annotations

import json

from riyansh_bs_integration.core.errors import IntegrationError


def get_customer_address(frappe, customer, preferred=None):
    address = preferred or frappe.db.get_value(
        "Customer", customer, "customer_primary_address"
    )

    if not address:
        rows = frappe.db.sql(
            """
            SELECT DISTINCT a.name
            FROM `tabAddress` a
            JOIN `tabDynamic Link` dl
              ON dl.parent = a.name
             AND dl.parenttype = 'Address'
            WHERE dl.link_doctype = 'Customer'
              AND dl.link_name = %s
            """,
            (customer,),
            as_dict=True,
        )
        if len(rows) == 1:
            address = rows[0].name

    if not address or not frappe.db.exists("Address", address):
        raise IntegrationError(
            "SHIPPING_ADDRESS_REQUIRED",
            "A valid customer shipping address is required",
            422,
            field="shipping_address",
        )

    linked = frappe.db.exists(
        "Dynamic Link",
        {
            "parenttype": "Address",
            "parent": address,
            "link_doctype": "Customer",
            "link_name": customer,
        },
    )
    if not linked:
        raise IntegrationError(
            "ADDRESS_CUSTOMER_MISMATCH",
            "Shipping address does not belong to customer",
            422,
            field="shipping_address",
        )

    state = frappe.db.get_value("Address", address, "state")
    if not state:
        raise IntegrationError(
            "ADDRESS_STATE_MISSING",
            "Customer shipping state is missing",
            422,
            field="shipping_address.state",
        )

    return address, str(state).strip()


def company_state(frappe, company):
    rows = frappe.db.sql(
        """
        SELECT DISTINCT a.state
        FROM `tabAddress` a
        JOIN `tabDynamic Link` dl
          ON dl.parent = a.name
         AND dl.parenttype = 'Address'
        WHERE dl.link_doctype = 'Company'
          AND dl.link_name = %s
          AND IFNULL(a.state, '') != ''
        """,
        (company,),
        as_dict=True,
    )

    states = {
        str(row.state).strip().casefold(): str(row.state).strip()
        for row in rows
    }

    if len(states) != 1:
        raise IntegrationError(
            "COMPANY_GST_STATE_AMBIGUOUS",
            "Company GST state must be unambiguous",
            503,
        )

    return next(iter(states.values()))


def get_gst_header(frappe, company, customer, address):
    origin = company_state(frappe, company)
    _, destination = get_customer_address(
        frappe, customer, address
    )

    abbr = frappe.db.get_value("Company", company, "abbr")
    if not abbr:
        raise IntegrationError(
            "COMPANY_ABBR_MISSING",
            "Company abbreviation is missing",
            503,
        )

    jurisdiction = (
        "In-state"
        if origin.casefold() == destination.casefold()
        else "Out-state"
    )

    template = f"Output GST {jurisdiction} - {abbr}"

    record = frappe.db.get_value(
        "Sales Taxes and Charges Template",
        template,
        ["company", "disabled"],
        as_dict=True,
    )

    if not record or record.company != company or record.disabled:
        raise IntegrationError(
            "GST_TEMPLATE_MISSING",
            f"Valid GST template not found: {template}",
            503,
        )

    return template


def get_item_tax_template(
    frappe, item_code, company, tax_heads, posting_date
):
    assignments = frappe.get_all(
        "Item Tax",
        filters={
            "parent": item_code,
            "parenttype": "Item",
        },
        fields=[
            "item_tax_template",
            "valid_from",
            "tax_category",
        ],
    )

    eligible = []

    for row in assignments:
        if row.valid_from and str(row.valid_from)[:10] > str(posting_date)[:10]:
            continue

        if row.tax_category:
            continue

        template_company = frappe.db.get_value(
            "Item Tax Template",
            row.item_tax_template,
            "company",
        )

        if template_company == company:
            eligible.append(row.item_tax_template)

    eligible = sorted(set(eligible))

    if len(eligible) != 1:
        raise IntegrationError(
            "ITEM_GST_AMBIGUOUS",
            f"Cannot resolve GST template for {item_code}",
            422,
            field="item_code",
        )

    template = eligible[0]

    details = frappe.get_all(
        "Item Tax Template Detail",
        filters={"parent": template},
        fields=["tax_type", "tax_rate"],
    )

    rates = {
        str(row.tax_type): float(row.tax_rate)
        for row in details
        if str(row.tax_type) in tax_heads
    }

    if set(rates) != set(tax_heads):
        raise IntegrationError(
            "ITEM_GST_ACCOUNTS_MISSING",
            f"GST account mapping incomplete for {item_code}",
            422,
            field="item_code",
        )

    return template, json.dumps(rates, sort_keys=True)


def set_document_gst(
    frappe, doc, company, customer, address, posting_date
):
    from erpnext.controllers.accounts_controller import (
        get_taxes_and_charges,
    )

    template = get_gst_header(
        frappe, company, customer, address
    )

    tax_rows = get_taxes_and_charges(
        "Sales Taxes and Charges Template",
        template,
    )

    if not tax_rows:
        raise IntegrationError(
            "GST_ROWS_MISSING",
            "GST template contains no tax rows",
            503,
        )

    doc.taxes_and_charges = template
    doc.set("taxes", [])

    for tax in tax_rows:
        doc.append("taxes", tax)

    heads = {
        str(row.account_head)
        for row in doc.taxes
    }

    for item in doc.items:
        item_template, rates = get_item_tax_template(
            frappe,
            item.item_code,
            company,
            heads,
            posting_date,
        )
        item.item_tax_template = item_template
        item.item_tax_rate = rates

    doc.ignore_pricing_rule = 1

    return template
