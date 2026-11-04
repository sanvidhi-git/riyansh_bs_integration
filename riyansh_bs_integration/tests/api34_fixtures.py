def order_payload():
    return {
        "bs_order_id": "BS-TEST-001",
        "customer_id": "CUSTOMER-001",
        "order_datetime": "2026-11-03T12:00:00+05:30",
        "warehouse_code": "SANGAMNER",
        "shipping_address": {
            "name": "Test Customer",
            "mobile": "9876543210",
            "address_line_1": "Test Road",
            "city": "Pune",
            "state": "Maharashtra",
            "pincode": "411001",
            "country": "India",
        },
        "items": [
            {"item_code": "RMALT001", "qty": 1,
             "uom": "Nos", "rate": 1000}
        ],
    }


def credit_payload():
    return {
        "bs_credit_note_id": "BS-CN-TEST-001",
        "bs_order_id": "BS-TEST-001",
        "customer_id": "CUSTOMER-001",
        "credit_note_datetime": "2026-11-03T14:00:00+05:30",
        "warehouse_code": "SANGAMNER",
        "items": [
            {"item_code": "RMALT001", "qty": 1, "rate": 1000}
        ],
    }
