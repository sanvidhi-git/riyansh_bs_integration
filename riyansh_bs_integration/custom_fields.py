CUSTOM_FIELDS = {
    "Customer": [
        {"fieldname": "custom_distributor_id", "label": "Distributor ID", "fieldtype": "Data", "unique": 1, "read_only": 1, "in_standard_filter": 1},
    ],
    "Supplier": [
        {"fieldname": "custom_distributor_id", "label": "Distributor ID", "fieldtype": "Data", "unique": 1, "read_only": 1, "in_standard_filter": 1},
    ],
    "Sales Order": [
        {"fieldname": "custom_bs_order_id", "label": "BS Order ID", "fieldtype": "Data", "unique": 1, "read_only": 1, "in_standard_filter": 1},
        {"fieldname": "custom_bs_payment_reference", "label": "BS Payment Reference", "fieldtype": "Data", "read_only": 1},
        {"fieldname": "custom_bs_source_datetime", "label": "BS Source Datetime", "fieldtype": "Datetime", "read_only": 1},
        {"fieldname": "custom_bs_request_fingerprint", "label": "BS Request Fingerprint", "fieldtype": "Data", "hidden": 1, "read_only": 1},
    ],
    "Sales Invoice": [
        {"fieldname": "custom_bs_credit_note_id", "label": "BS Credit Note ID", "fieldtype": "Data", "unique": 1, "read_only": 1, "in_standard_filter": 1},
        {"fieldname": "custom_bs_order_id", "label": "BS Order ID", "fieldtype": "Data", "read_only": 1},
        {"fieldname": "custom_bs_source_datetime", "label": "BS Source Datetime", "fieldtype": "Datetime", "read_only": 1},
        {"fieldname": "custom_bs_request_fingerprint", "label": "BS Request Fingerprint", "fieldtype": "Data", "hidden": 1, "read_only": 1},
    ],
}
