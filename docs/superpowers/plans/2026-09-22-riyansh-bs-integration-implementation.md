# Riyansh BS Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a standalone Frappe v16 app named `riyansh_bs_integration` implementing three authenticated BS-to-ERP POST APIs and one durable ERP-to-BS KYC-result POST flow.

**Architecture:** The app owns API transport, validation, audit records, KYC workflow and additive ERPNext custom fields. Thin whitelisted API functions call isolated services; services create ERP documents in controlled transactions; a durable outbound event queue delivers KYC results after commit. The app has no source or runtime dependency on the old `riyansh_integration` app.

**Tech Stack:** Python 3.14, Frappe 16, ERPNext 16, MariaDB, Redis/RQ, `requests`, Frappe Test Framework, Postman Collection v2.1.

**Spec:** `docs/superpowers/specs/2026-09-22-riyansh-bs-integration-design.md`

## Global Constraints

- Do not modify Frappe core, ERPNext core or `riyansh_integration`.
- Use the new package and app name `riyansh_bs_integration` exclusively.
- Keep every schema migration additive and idempotent.
- Never permit Guest access on integration endpoints.
- Never log Aadhaar, bank account, file bytes, API keys, tokens or passwords unmasked.
- Create Customer and Supplier only after KYC PASS; store the same Distributor ID on both and create Party Link.
- Keep Sales Orders and return Sales Invoices in Draft unless the disabled-by-default setting is explicitly enabled after UAT.
- Do not enable Common Party Accounting automatically.
- Do not use existing Riyansh operational records in tests.

## Review Focus

1. Concurrent identical POSTs must create exactly one onboarding/order/credit-note record; Tasks 4, 7 and 8 include concurrency/idempotency tests.
2. A database rollback must not leave a Customer without its Supplier/Party Link or emit a KYC result; Task 5 includes rollback and after-commit tests.
3. Multipart requests must reject missing, oversized, executable or misleadingly named files; Task 4 tests content type, extension, size and required parts.
4. Return quantities must be cumulative across earlier returns and never exceed the original submitted invoice quantity; Task 8 tests partial and repeat returns.
5. Sensitive values must remain masked in both successful and failed logs, including nested JSON and multipart metadata; Tasks 2 and 4 include recursive-redaction tests.

---

### Task 1: Scaffold the standalone Frappe app

**Files:**
- Create: `riyansh_bs_integration/__init__.py`
- Create: `riyansh_bs_integration/hooks.py`
- Create: `riyansh_bs_integration/modules.txt`
- Create: `riyansh_bs_integration/patches.txt`
- Create: `riyansh_bs_integration/riyansh_bs_integration/__init__.py`
- Create: `riyansh_bs_integration/api/__init__.py`
- Create: `riyansh_bs_integration/api/v1/__init__.py`
- Create: `riyansh_bs_integration/services/__init__.py`
- Create: `riyansh_bs_integration/core/__init__.py`
- Create: `pyproject.toml`
- Create: `license.txt`
- Create: `README.md`
- Test: `riyansh_bs_integration/tests/test_app_boundary.py`

**Interfaces:**
- Consumes: Frappe app discovery conventions.
- Produces: installable app `riyansh_bs_integration` version `0.1.0` with scheduled entry point `riyansh_bs_integration.core.outbound.process_due_events`.

- [ ] **Step 1: Write the boundary test**

```python
from pathlib import Path


def test_new_app_does_not_import_old_app():
    root = Path(__file__).parents[2]
    python_text = "\n".join(p.read_text(encoding="utf-8") for p in root.rglob("*.py"))
    assert "from riyansh_integration" not in python_text
    assert "import riyansh_integration" not in python_text
```

- [ ] **Step 2: Run the test and confirm it fails because the package is absent**

Run: `pytest -q riyansh_bs_integration/tests/test_app_boundary.py`  
Expected: FAIL because the test/package path does not yet exist.

- [ ] **Step 3: Create the package metadata and hooks**

Set `app_name = "riyansh_bs_integration"`, `app_title = "Riyansh BS Integration"`, `app_publisher = "Sanvidhi Tech"`, `app_version = "0.1.0"`, and define only these lifecycle hooks:

```python
after_install = "riyansh_bs_integration.install.after_install"
after_migrate = "riyansh_bs_integration.install.after_migrate"
scheduler_events = {
    "cron": {
        "*/5 * * * *": ["riyansh_bs_integration.core.outbound.process_due_events"]
    }
}
```

- [ ] **Step 4: Run boundary and import compilation checks**

Run: `pytest -q riyansh_bs_integration/tests/test_app_boundary.py && python -m compileall -q riyansh_bs_integration`  
Expected: PASS and exit code 0.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml license.txt README.md riyansh_bs_integration
git commit -m "chore: scaffold standalone Riyansh BS integration app"
```

### Task 2: Build shared responses, validation, authentication and safe logging

**Files:**
- Create: `riyansh_bs_integration/core/errors.py`
- Create: `riyansh_bs_integration/core/responses.py`
- Create: `riyansh_bs_integration/core/validation.py`
- Create: `riyansh_bs_integration/core/auth.py`
- Create: `riyansh_bs_integration/core/logging.py`
- Test: `riyansh_bs_integration/tests/test_core.py`

**Interfaces:**
- Produces: `IntegrationError(code, message, http_status, field=None, details=None)`, `success(data, message, http_status)`, `failure(error, correlation_id)`, `require_integration_access()`, `request_fingerprint(payload)`, `redact(value)` and `integration_endpoint(interface, reference_key)`.

- [ ] **Step 1: Write failing core-contract tests**

```python
def test_recursive_redaction_masks_nested_secrets():
    value = {"aadhaar_number": "123412341234", "bank": {"account_number": "998877"}}
    assert redact(value) == {"aadhaar_number": "********1234", "bank": {"account_number": "**8877"}}


def test_fingerprint_is_stable_across_key_order():
    assert request_fingerprint({"b": 2, "a": 1}) == request_fingerprint({"a": 1, "b": 2})
```

- [ ] **Step 2: Run tests to verify missing imports/functions fail**

Run: `pytest -q riyansh_bs_integration/tests/test_core.py`  
Expected: FAIL with missing core modules.

- [ ] **Step 3: Implement the shared contracts**

Use canonical JSON (`sort_keys=True`, compact separators) and SHA-256 for fingerprints. Redact keys including `aadhaar`, `aadhaar_number`, `account_number`, `api_key`, `api_secret`, `authorization`, `token`, `password`, `file`, and `content`. `require_integration_access()` must reject Guest, require role `Riyansh BS Integration`, check Integration Enabled/Inbound Enabled, and enforce configured IP/CIDR values.

- [ ] **Step 4: Add API decorator transaction/log tests**

Test 201 creation, 200 duplicate, 422 validation, 409 conflict and unexpected 500 envelopes. Verify the API log receives a masked request even when the wrapped service raises and the database mutation rolls back.

- [ ] **Step 5: Run tests and compile**

Run: `pytest -q riyansh_bs_integration/tests/test_core.py && python -m compileall -q riyansh_bs_integration/core`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add riyansh_bs_integration/core riyansh_bs_integration/tests/test_core.py
git commit -m "feat: add secure integration request foundation"
```

### Task 3: Add settings, operational DocTypes, roles and custom fields

**Files:**
- Create: `riyansh_bs_integration/install.py`
- Create: `riyansh_bs_integration/custom_fields.py`
- Create: `riyansh_bs_integration/riyansh_bs_integration/doctype/bs_integration_settings/*`
- Create: `riyansh_bs_integration/riyansh_bs_integration/doctype/bs_distributor_onboarding/*`
- Create: `riyansh_bs_integration/riyansh_bs_integration/doctype/bs_api_log/*`
- Create: `riyansh_bs_integration/riyansh_bs_integration/doctype/bs_outbound_event/*`
- Test: `riyansh_bs_integration/tests/test_installation.py`

**Interfaces:**
- Produces: Single `BS Integration Settings`; transactional DocTypes named `BS Distributor Onboarding`, `BS API Log`, and `BS Outbound Event`; roles `Riyansh BS Integration`, `Riyansh KYC Verifier`, and `Riyansh KYC Approver`.

- [ ] **Step 1: Write failing installation tests**

```python
def test_custom_fields_are_additive_and_repeatable():
    create_custom_fields()
    create_custom_fields()
    assert frappe.get_meta("Customer").has_field("custom_distributor_id")
    assert frappe.get_meta("Supplier").has_field("custom_distributor_id")
    assert frappe.get_meta("Sales Order").has_field("custom_bs_order_id")
    assert frappe.get_meta("Sales Invoice").has_field("custom_bs_credit_note_id")
```

- [ ] **Step 2: Run with a disposable Frappe test site**

Run: `bench --site test_site run-tests --app riyansh_bs_integration --module riyansh_bs_integration.tests.test_installation`  
Expected: FAIL because DocTypes/fields do not exist.

- [ ] **Step 3: Implement DocType JSON/controllers and install hooks**

Define exact fields from the approved spec. Use Password fields for outbound credentials; Attach fields must reference private files. Make BS API Log append-only by rejecting update/delete in its controller. Do not create twelve legacy endpoint rows and do not enable Common Party Accounting.

- [ ] **Step 4: Implement additive standard custom fields**

Use `create_custom_fields(..., update=False)` for Customer, Supplier, Sales Order and Sales Invoice fields from the spec. Mark external IDs read-only after set, indexed and unique where specified.

- [ ] **Step 5: Run installation twice and verify existing records are unchanged**

Run: `bench --site test_site migrate && bench --site test_site migrate && bench --site test_site run-tests --app riyansh_bs_integration --module riyansh_bs_integration.tests.test_installation`  
Expected: PASS with one copy of each field/role and no deleted records.

- [ ] **Step 6: Commit**

```bash
git add riyansh_bs_integration/install.py riyansh_bs_integration/custom_fields.py riyansh_bs_integration/riyansh_bs_integration/doctype riyansh_bs_integration/tests/test_installation.py
git commit -m "feat: add integration settings records and custom fields"
```

### Task 4: Implement distributor multipart submission API

**Files:**
- Create: `riyansh_bs_integration/api/v1/distributor.py`
- Create: `riyansh_bs_integration/services/distributor_service.py`
- Test: `riyansh_bs_integration/tests/test_distributor_api.py`

**Interfaces:**
- Consumes: `integration_endpoint`, validation utilities and `BS Distributor Onboarding`.
- Produces: whitelisted POST `riyansh_bs_integration.api.v1.distributor.submit()` and `submit_distributor(payload, files, correlation_id)`.

- [ ] **Step 1: Write failing happy-path and file-validation tests**

Test a valid multipart payload plus four small PNG/PDF fixtures, and separately test a missing Aadhaar back, 5 MB limit breach, `.exe`, MIME/extension mismatch and empty document.

- [ ] **Step 2: Run tests and verify endpoint is absent**

Run: `bench --site test_site run-tests --app riyansh_bs_integration --module riyansh_bs_integration.tests.test_distributor_api`  
Expected: FAIL with missing endpoint/service.

- [ ] **Step 3: Implement schema and identity validation**

Validate mandatory fields, ISO date/timestamp, Indian mobile/pincode, PAN pattern `[A-Z]{5}[0-9]{4}[A-Z]`, Aadhaar as 12 digits, and IFSC `[A-Z]{4}0[A-Z0-9]{6}`. Treat identifiers and account numbers as strings.

- [ ] **Step 4: Implement private file persistence and idempotency**

Create files with `is_private=1` and attach them to the onboarding document. Use unique Distributor ID plus request fingerprint. Identical retry returns HTTP 200; conflicting identity returns 409; pending updates preserve Version history; approved sensitive changes move status to Under Review.

- [ ] **Step 5: Add concurrent duplicate test**

Send simultaneous requests with the same Distributor ID and assert exactly one `BS Distributor Onboarding` exists. Catch the database unique-key race and return the winning record.

- [ ] **Step 6: Run distributor tests**

Run: `bench --site test_site run-tests --app riyansh_bs_integration --module riyansh_bs_integration.tests.test_distributor_api`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add riyansh_bs_integration/api/v1/distributor.py riyansh_bs_integration/services/distributor_service.py riyansh_bs_integration/tests/test_distributor_api.py
git commit -m "feat: add distributor and KYC intake API"
```

### Task 5: Implement KYC decision, dual-party creation and Party Link

**Files:**
- Create: `riyansh_bs_integration/services/kyc_service.py`
- Modify: `riyansh_bs_integration/riyansh_bs_integration/doctype/bs_distributor_onboarding/bs_distributor_onboarding.py`
- Test: `riyansh_bs_integration/tests/test_kyc_workflow.py`

**Interfaces:**
- Produces: `approve_kyc(onboarding_name, decision_user)` and `reject_kyc(onboarding_name, reason_code, reason, decision_user)`.

- [ ] **Step 1: Write failing PASS/FAIL authorization tests**

Verify BS integration users cannot decide KYC; verifier/approver roles follow maker-checker rules; FAIL requires both reason fields.

- [ ] **Step 2: Write failing party-creation test**

Assert PASS creates one Customer, one Supplier, the same `custom_distributor_id` on both, and one Party Link connecting them. Assert a second approval creates no duplicate.

- [ ] **Step 3: Implement PASS and FAIL transactions**

Create Customer/Supplier using configured groups/territory and shared Address/Contact links. Create Party Link without enabling Common Party Accounting. Create the outbound event only through an after-commit enqueue path.

- [ ] **Step 4: Add rollback test**

Force Supplier creation to fail after Customer insertion and assert Customer, Supplier, Party Link and outbound event counts all remain zero after rollback.

- [ ] **Step 5: Run KYC workflow tests**

Run: `bench --site test_site run-tests --app riyansh_bs_integration --module riyansh_bs_integration.tests.test_kyc_workflow`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add riyansh_bs_integration/services/kyc_service.py riyansh_bs_integration/riyansh_bs_integration/doctype/bs_distributor_onboarding riyansh_bs_integration/tests/test_kyc_workflow.py
git commit -m "feat: add KYC workflow and linked party creation"
```

### Task 6: Implement durable outbound KYC-result delivery

**Files:**
- Create: `riyansh_bs_integration/core/outbound.py`
- Test: `riyansh_bs_integration/tests/test_kyc_outbound.py`

**Interfaces:**
- Produces: `queue_kyc_result(onboarding_name)`, `dispatch_event(event_name)`, `process_due_events()` and `retry_event(event_name)`.

- [ ] **Step 1: Write failing payload tests**

Assert PASS includes distributor, Customer, Supplier, PASS and null failure fields; FAIL includes null ERP IDs, FAIL, reason code/reason and ISO-8601 timestamp.

- [ ] **Step 2: Write failing HTTP-behaviour tests using mocked requests**

Cover 2xx Delivered, timeout retry, network error retry, 429 retry, 5xx retry, 400 permanent failure, 401/403 permanent authentication failure and response-body truncation.

- [ ] **Step 3: Implement dispatch and bounded retry**

Read URL and encrypted credentials from settings, set JSON headers, never log raw Authorization, increment attempts atomically, and use `[60, 300, 900, 3600]` second backoff capped by configured maximum attempts.

- [ ] **Step 4: Implement authorised manual retry**

Permit only System Manager or Riyansh KYC Approver to reset a Failed KYC event. Keep the same event ID and payload so BS can deduplicate it.

- [ ] **Step 5: Run outbound tests**

Run: `bench --site test_site run-tests --app riyansh_bs_integration --module riyansh_bs_integration.tests.test_kyc_outbound`  
Expected: PASS without real network calls.

- [ ] **Step 6: Commit**

```bash
git add riyansh_bs_integration/core/outbound.py riyansh_bs_integration/tests/test_kyc_outbound.py
git commit -m "feat: add reliable KYC result delivery"
```

### Task 7: Implement confirmed-order POST API

**Files:**
- Create: `riyansh_bs_integration/api/v1/order.py`
- Create: `riyansh_bs_integration/services/order_service.py`
- Test: `riyansh_bs_integration/tests/test_order_api.py`

**Interfaces:**
- Produces: whitelisted POST `riyansh_bs_integration.api.v1.order.create(payload=None)` and `create_sales_order(payload, correlation_id)`.

- [ ] **Step 1: Write failing valid-order test**

Create isolated Item, Warehouse, verified onboarding/Customer, Item Price and stock fixtures. Submit the approved payload and assert one Draft Sales Order with mapped Customer, warehouse, items, amounts and BS custom fields.

- [ ] **Step 2: Write failing validation tests**

Cover unknown/unverified distributor, missing Customer, disabled/unknown item, invalid UOM, unknown warehouse code, zero/negative quantity, stale price beyond tolerance, mismatched subtotal/tax/grand total, invalid paid-without-reference, and insufficient stock.

- [ ] **Step 3: Implement mapping and arithmetic validation**

Recalculate every line and order total using Decimal semantics and configured tolerances. Resolve distributor through `custom_distributor_id`; resolve warehouse only from configured mapping; never accept an arbitrary ERP Warehouse name from BS.

- [ ] **Step 4: Implement idempotent Draft Sales Order creation**

Use `custom_bs_order_id` as a unique key and request fingerprint in the API log. Exact retry returns HTTP 200 and the original Sales Order; conflicting retry returns 409.

- [ ] **Step 5: Add simultaneous-request test**

Send ten same-ID requests and assert exactly one Sales Order exists and all successful results reference it.

- [ ] **Step 6: Run order tests**

Run: `bench --site test_site run-tests --app riyansh_bs_integration --module riyansh_bs_integration.tests.test_order_api`  
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add riyansh_bs_integration/api/v1/order.py riyansh_bs_integration/services/order_service.py riyansh_bs_integration/tests/test_order_api.py
git commit -m "feat: add confirmed order intake API"
```

### Task 8: Implement approved credit-note POST API

**Files:**
- Create: `riyansh_bs_integration/api/v1/credit_note.py`
- Create: `riyansh_bs_integration/services/credit_note_service.py`
- Test: `riyansh_bs_integration/tests/test_credit_note_api.py`

**Interfaces:**
- Produces: whitelisted POST `riyansh_bs_integration.api.v1.credit_note.create(payload=None)` and `create_credit_note(payload, correlation_id)`.

- [ ] **Step 1: Write failing valid-credit-note test**

Build a submitted Sales Invoice fixture and assert APPROVED input creates one Draft Sales Invoice with `is_return=1`, `return_against`, negative quantities/amounts generated by ERPNext mapping, no automatic stock movement and the BS custom IDs.

- [ ] **Step 2: Write failing validation tests**

Cover non-APPROVED status, missing/submitted-original invoice, distributor/customer mismatch, item absent from original invoice, zero quantity, rate/tax mismatch and original invoice already fully returned.

- [ ] **Step 3: Implement cumulative returnable-quantity calculation**

For each item row, subtract quantities in non-cancelled submitted return invoices from the original invoiced quantity. Reject a request that exceeds the remaining quantity.

- [ ] **Step 4: Implement idempotency and concurrency protection**

Exact retry returns the existing Draft credit note; conflicting same ID returns 409; simultaneous requests create exactly one return invoice.

- [ ] **Step 5: Run credit-note tests**

Run: `bench --site test_site run-tests --app riyansh_bs_integration --module riyansh_bs_integration.tests.test_credit_note_api`  
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add riyansh_bs_integration/api/v1/credit_note.py riyansh_bs_integration/services/credit_note_service.py riyansh_bs_integration/tests/test_credit_note_api.py
git commit -m "feat: add approved credit note intake API"
```

### Task 9: Add Postman collection, deployment checks and complete verification

**Files:**
- Create: `Riyansh_BS_Integration.postman_collection.json`
- Create: `docs/TESTING.md`
- Create: `docs/DEPLOYMENT.md`
- Modify: `README.md`
- Test: all app tests

**Interfaces:**
- Produces: importable Postman collection with environment variables `base_url`, `api_key`, `api_secret`; test-site deployment and rollback instructions.

- [ ] **Step 1: Create Postman requests and assertions**

Include API 1 multipart success/duplicate/invalid-file cases, API 3 success/duplicate/invalid-total cases and API 4 success/over-return cases. Scripts must assert status, `success`, correlation ID and returned ERP reference. Do not include real keys, PII or production URLs.

- [ ] **Step 2: Document API 2 testing**

Document mocked automated tests followed by a BS test-URL request using one controlled distributor. Record the expected acknowledgement and manual retry procedure.

- [ ] **Step 3: Document safe test deployment**

Require site/database/files backup, test-image build, `bench --site test.riyansh.sanvidhierp.in install-app riyansh_bs_integration`, migrate, role/user setup, settings configuration and smoke tests. Explicitly prohibit installing or changing the old app.

- [ ] **Step 4: Run complete verification**

Run:

```bash
python -m compileall -q riyansh_bs_integration
bench --site test_site run-tests --app riyansh_bs_integration
git diff --check
git status --short
```

Expected: compile success, all tests PASS, no whitespace errors, and only intentional files tracked.

- [ ] **Step 5: Verify no forbidden dependency or secret**

Run:

```bash
rg -n "from riyansh_integration|import riyansh_integration" . && exit 1 || true
rg -n -i "(api_secret|bearer_token|password)\s*[:=]\s*['\"][^'{]" . && exit 1 || true
```

Expected: no old-app import and no hard-coded credential match.

- [ ] **Step 6: Commit**

```bash
git add README.md docs Riyansh_BS_Integration.postman_collection.json
git commit -m "docs: add integration testing and deployment handoff"
```

### Task 10: Prepare the reviewable source package

**Files:**
- Create: `dist/riyansh_bs_integration-source.zip`
- Test: archive manifest and clean checkout verification

**Interfaces:**
- Consumes: completed app and passing verification from Tasks 1-9.
- Produces: source ZIP for review; deployment remains a separate approved action.

- [ ] **Step 1: Create the archive from tracked files only**

Run: `git archive --format=zip --output=dist/riyansh_bs_integration-source.zip HEAD`  
Expected: ZIP created without `.git`, secrets, caches or local test data.

- [ ] **Step 2: Inspect archive manifest**

Run: `unzip -l dist/riyansh_bs_integration-source.zip`  
Expected: new app, tests, docs and Postman collection only; no `riyansh_integration` folder.

- [ ] **Step 3: Verify a clean extraction compiles**

Extract into a temporary directory and run `python -m compileall -q riyansh_bs_integration`.  
Expected: exit code 0.

- [ ] **Step 4: Record final commit and checksum**

Run: `git rev-parse HEAD && sha256sum dist/riyansh_bs_integration-source.zip`  
Expected: one commit hash and one SHA-256 checksum for handoff.

