# Riyansh BS Integration Design

**Date:** 22 September 2026  
**Status:** Approved conversational design, written specification for review  
**App:** `riyansh_bs_integration`  
**Target:** Frappe/ERPNext version 16, Riyansh test site first

## 1. Goal

Create a new, independent Frappe custom app that supports exactly four Business Software (BS) integration flows:

1. BS submits distributor and KYC data to ERPNext.
2. ERPNext sends the final KYC PASS/FAIL result to BS.
3. BS submits a confirmed order to ERPNext.
4. BS submits an approved credit-note/return adjustment to ERPNext.

All other sales, stock, tax, invoice, fulfilment, payment, payout and accounting operations remain internal to ERPNext.

## 2. Non-negotiable boundaries

- Do not modify Frappe core or ERPNext core.
- Do not modify, install, import or depend on the existing `riyansh_integration` app.
- The old app may be consulted only as a read-only reference.
- Do not delete or rewrite existing Riyansh masters or transactions.
- Develop and test locally before installing on `test.riyansh.sanvidhierp.in`.
- Production deployment is outside this phase and requires separate approval.
- Installation patches must be additive and idempotent.
- Common Party Accounting must not be enabled automatically.

## 3. Integration ownership

| Flow | Method | Endpoint owner | Caller | Direction |
|---|---|---|---|---|
| Distributor submission | POST | ERPNext | BS | BS to ERPNext |
| KYC result | POST | BS | ERPNext | ERPNext to BS |
| Confirmed order | POST | ERPNext | BS | BS to ERPNext |
| Credit note | POST | ERPNext | BS | BS to ERPNext |

ERPNext exposes three authenticated endpoints. The KYC result is an outbound POST to a BS-provided test URL.

## 4. New app structure

The app is standalone and organized by responsibility:

```text
riyansh_bs_integration/
  api/v1/
    distributor.py
    order.py
    credit_note.py
  services/
    distributor_service.py
    kyc_service.py
    order_service.py
    credit_note_service.py
  core/
    auth.py
    errors.py
    logging.py
    responses.py
    validation.py
  doctype/
    bs_integration_settings/
    bs_distributor_onboarding/
    bs_api_log/
    bs_outbound_event/
  custom_fields.py
  hooks.py
  install.py
  tests/
```

API modules parse requests and return responses. Services contain business logic. Core modules provide shared security, validation, response and logging behaviour.

## 5. Internal DocTypes

### 5.1 BS Integration Settings

A Single DocType containing:

- Integration Enabled
- Inbound Enabled
- KYC Outbound Enabled
- Environment: Test or Production
- Company
- Default Customer Group
- Default Supplier Group
- Default Territory
- Default Price List
- Default Warehouse
- KYC Result URL
- BS authentication type
- Encrypted BS credential fields
- Allowed IP addresses/CIDRs
- Maximum document size, default 5 MB
- Auto-submit Sales Order, default disabled
- Auto-submit Credit Note, default disabled
- Maximum outbound attempts
- Request timeout

No real credential is stored in source control.

### 5.2 BS Distributor Onboarding

Stores the submitted distributor/KYC record before Customer and Supplier creation:

- unique Distributor ID
- member and nominee details
- mobile and email
- date of birth
- complete address
- PAN and Aadhaar
- bank and account details
- private PAN, Aadhaar front/back and cancelled-cheque files
- KYC status: Pending, Under Review, Passed or Failed
- failure code and reason
- Customer link
- Supplier link
- verification user and timestamp
- KYC outbound delivery status
- source timestamp, correlation ID and request fingerprint

### 5.3 BS API Log

Append-only operational log containing interface, direction, correlation ID, BS reference, user, masked request, response, HTTP status, duration and processing result. Aadhaar, account number, document bytes, API keys, tokens and passwords are never stored unmasked.

### 5.4 BS Outbound Event

Durable KYC-result delivery record containing event ID, distributor, target URL, masked payload, attempt count, status, next retry time, acknowledgement and last error. This prevents a temporary BS outage from losing a KYC result.

## 6. Additive custom fields

Fields are created only when absent and never overwrite existing fields.

| ERPNext DocType | Label | Fieldname | Rules |
|---|---|---|---|
| Customer | Distributor ID | `custom_distributor_id` | Unique, indexed, read-only after set |
| Supplier | Distributor ID | `custom_distributor_id` | Unique, indexed, read-only after set |
| Sales Order | BS Order ID | `custom_bs_order_id` | Unique and indexed |
| Sales Order | BS Payment Reference | `custom_bs_payment_reference` | Indexed when populated |
| Sales Order | BS Source Datetime | `custom_bs_source_datetime` | Datetime |
| Sales Invoice | BS Credit Note ID | `custom_bs_credit_note_id` | Unique and indexed |
| Sales Invoice | BS Order ID | `custom_bs_order_id` | Indexed |
| Sales Invoice | BS Source Datetime | `custom_bs_source_datetime` | Datetime |

## 7. API 1: Distributor submission

### Endpoint

```text
POST /api/method/riyansh_bs_integration.api.v1.distributor.submit
```

### Transport

`multipart/form-data` with:

- `payload`: JSON text
- `pan_card`: PDF/JPG/JPEG/PNG
- `aadhaar_front`: PDF/JPG/JPEG/PNG
- `aadhaar_back`: PDF/JPG/JPEG/PNG
- `cancelled_cheque`: PDF/JPG/JPEG/PNG

Each file is limited to 5 MB by default and stored as a private Frappe File attached to BS Distributor Onboarding.

### JSON payload

```json
{
  "distributor_id": "RM6110738",
  "member_name": "Sample Member",
  "enterprise_name": null,
  "nominee_name": null,
  "nominee_relationship": null,
  "mobile": "9876543210",
  "email": null,
  "date_of_birth": "2000-08-03",
  "address": {
    "address_line_1": "Sample address",
    "address_line_2": null,
    "city": "Mouda",
    "district": "Nagpur",
    "state": "Maharashtra",
    "pincode": "441104",
    "country": "India"
  },
  "pan_number": "ABCDE1234F",
  "aadhaar_number": "486890652630",
  "bank": {
    "bank_name": "Sample Bank",
    "branch_name": "Sample Branch",
    "ifsc_code": "ABCD0001234",
    "account_number": "10900100019975"
  },
  "remark": null,
  "source_created_at": "2026-09-22T09:30:00+05:30"
}
```

`distributor_id` is the immutable idempotency key. An identical retry returns the existing onboarding record. Updates while KYC is pending update the same record. A sensitive identity or bank change after approval moves the record back to review; it must never silently modify approved masters.

The API creates no Customer or Supplier until KYC passes.

## 8. KYC processing and API 2

### PASS workflow

An authorised KYC user marks the onboarding record Passed. In one database transaction, the app:

1. Rechecks unique distributor ID, PAN, Aadhaar, mobile and bank account.
2. Creates or safely resolves one Customer.
3. Creates or safely resolves one Supplier.
4. Stores the same Distributor ID in both custom fields.
5. Creates a Party Link between Customer and Supplier.
6. Updates the onboarding links and verification audit fields.
7. Creates a durable KYC outbound event after the database transaction succeeds.

### FAIL workflow

The authorised user must select a failure code and enter a human-readable reason. No Customer, Supplier or Party Link is created. A durable failure event is created.

### Outbound payload

```json
{
  "distributor_id": "RM6110738",
  "erp_customer_id": "CUST-00001",
  "erp_supplier_id": "SUPP-00001",
  "verification_status": "PASS",
  "failure_reason_code": null,
  "failure_reason": null,
  "verified_at": "2026-09-22T11:30:00+05:30"
}
```

For FAIL, ERP IDs are null and both failure fields are required. ERPNext POSTs this payload to the configured BS URL using the authentication method agreed with BS. Successful 2xx responses mark the event Delivered. Timeouts, network errors, 429 and 5xx responses retry with bounded backoff. Permanent 4xx validation/authentication failures become Failed and require an authorised manual retry after correction.

Common Party Accounting remains a manually controlled Accounts Setting. The app creates the Party Link but does not enable site-wide automatic accounting behaviour.

## 9. API 3: Confirmed order

### Endpoint

```text
POST /api/method/riyansh_bs_integration.api.v1.order.create
```

The JSON contains `bs_order_id`, distributor, order datetime, warehouse code, payment details, shipping address, item rows and reconciled subtotal/discount/tax/shipping/grand totals.

Before creation, the app validates:

- unique immutable `bs_order_id`
- existing KYC-passed distributor
- linked Customer
- configured company and warehouse mapping
- active stock Items and UOMs
- positive quantities
- ERP price/tolerance policy
- tax and total arithmetic
- stock availability when enabled
- valid payment status/reference combination

The app creates one Draft Sales Order by default. Auto-submit is a disabled configuration switch that may be enabled only after UAT. An exact retry returns the existing Sales Order. A conflicting payload for the same BS Order ID returns HTTP 409 and never alters the original transaction.

## 10. API 4: Credit note

### Endpoint

```text
POST /api/method/riyansh_bs_integration.api.v1.credit_note.create
```

The JSON contains `bs_credit_note_id`, original BS Order ID, distributor, original ERP Sales Invoice reference, approved status, reason, warehouse, item rows and totals.

Before creation, the app validates:

- unique immutable `bs_credit_note_id`
- status equals APPROVED
- original BS order and submitted ERP Sales Invoice exist
- distributor/customer matches the original invoice
- returned items exist on the original invoice
- each returned quantity does not exceed the remaining returnable quantity
- rates, taxes and totals reconcile to the original invoice

The app creates a Draft return Sales Invoice with `is_return = 1` and `return_against` set to the original Sales Invoice. It does not automatically move stock; physical stock receipt remains an internal warehouse-controlled ERPNext process. Auto-submit remains disabled until UAT approval. Duplicate retries return the existing draft; conflicting reuse of the same ID returns HTTP 409.

## 11. Authentication and authorization

- No endpoint permits Guest access.
- BS uses a dedicated System User with a least-privilege `Riyansh BS Integration` role.
- Authentication uses Frappe token authentication over HTTPS.
- The custom endpoint guard requires the integration role, integration switch and optional IP allow-list.
- KYC approval/rejection requires dedicated KYC roles and cannot be performed by the BS integration user.
- API secrets and BS outbound credentials are encrypted/stored through supported Frappe mechanisms and excluded from logs and source control.
- Rate limiting is enforced at the reverse proxy and, where configured, the application layer.

## 12. Response contract

Success responses contain:

```json
{
  "success": true,
  "message": "Accepted",
  "data": {},
  "correlation_id": "COR-20260922-000001"
}
```

Failure responses contain:

```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed.",
    "field": "field_name",
    "details": []
  },
  "correlation_id": "COR-20260922-000001"
}
```

HTTP statuses are 200 for duplicate-safe success, 201 for creation, 400 for malformed requests, 401 for missing/invalid authentication, 403 for role/IP rejection, 409 for identity/idempotency conflicts, 413 for oversized files, 415 for unsupported media, 422 for semantic validation and 500 only for unexpected failures.

## 13. Transaction and failure rules

- Each inbound request uses a single controlled database transaction.
- Partial Customer/Supplier, Sales Order or Credit Note creation is rolled back.
- External KYC delivery occurs only after the ERP transaction commits.
- Request fingerprints distinguish harmless retries from conflicting duplicates.
- Logs must still record rejected and rolled-back requests without exposing sensitive values.
- Unexpected exceptions return a generic message; full tracebacks remain server-side.
- Operators receive a manual retry mechanism only for outbound KYC delivery, not for replaying arbitrary inbound mutations.

## 14. Testing

Automated tests cover:

- authentication and integration-role enforcement
- disabled integration and IP rejection
- valid distributor multipart submission
- missing/oversized/unsupported KYC files
- invalid PAN, Aadhaar, IFSC, mobile, pincode and dates
- identical and conflicting distributor retries
- KYC PASS creation of Customer, Supplier and Party Link
- KYC FAIL without party creation
- masking of Aadhaar, bank accounts, files and secrets
- KYC outbound success, timeout, retry and permanent rejection
- valid Draft Sales Order creation
- unknown/unverified distributor, item or warehouse rejection
- insufficient stock and mismatched totals
- simultaneous duplicate order requests creating exactly one document
- valid return Sales Invoice creation
- over-return, wrong invoice/customer/item and duplicate credit-note rejection
- rollback on failures

Postman testing uses controlled test identifiers and the test site. Tests must not use existing Riyansh operational records. API 2 is tested first with a controlled mock receiver, then with the BS test URL and credentials.

## 15. Deployment sequence

1. Create a new Git repository/branch for `riyansh_bs_integration`.
2. Implement locally using tests first.
3. Build the app into a new test image without modifying Frappe/ERPNext core.
4. Take test-site database and private/public file backups.
5. Install only the new app on the Riyansh test site.
6. Run migrations and verify additive fields/DocTypes.
7. Create the restricted integration user and configure test settings.
8. Execute automated tests and Postman negative/positive cases.
9. Run controlled end-to-end tests jointly with BS.
10. Obtain written UAT and finance approval before any production work.

## 16. Acceptance criteria

- The old `riyansh_integration`, Frappe and ERPNext source remain unchanged.
- Only the three confirmed inbound endpoints are exposed to BS.
- ERPNext sends only the confirmed KYC result to BS.
- Every BS reference is idempotent and duplicates cannot create extra masters or transactions.
- KYC PASS creates Customer, Supplier and Party Link with the same Distributor ID.
- KYC FAIL creates no party masters.
- Orders and credit notes are Draft by default on the test site.
- Sensitive KYC and authentication information is private and masked.
- All four flows pass automated and Postman testing before URLs are shared for vendor testing.
