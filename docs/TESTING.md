# Test guide

## Portable checks

```bash
python -m unittest discover -s riyansh_bs_integration/tests -v
python -m compileall -q riyansh_bs_integration
git diff --check
```

## Required Bench validation

Use a disposable Frappe/ERPNext v16 site. Never use production records.

```bash
bench --site test_site install-app riyansh_bs_integration
bench --site test_site migrate
bench --site test_site run-tests --app riyansh_bs_integration
```

Create isolated Company, warehouse, Item, Item Price and stock fixtures. Test:

- identical retries and conflicting external IDs;
- two simultaneous submissions for each inbound API;
- missing, empty, oversized and MIME-mismatched KYC files;
- KYC PASS creates one Customer, one Supplier and one Party Link;
- a forced Supplier failure rolls the full KYC transaction back;
- order rejection for unverified distributor, stale price and low stock;
- cumulative partial returns cannot exceed the submitted original invoice;
- KYC delivery for 2xx, 400, 401/403, 429, 5xx and network timeout.

## Postman

Import `Riyansh_BS_Integration.postman_collection.json`. Set only test values:

- `base_url`: `https://test.riyansh.sanvidhierp.in`
- `api_key` and `api_secret`: dedicated limited System User credentials
- test IDs/references required by each request

Never put real secrets or Aadhaar/bank data in the collection. API 2 is tested
by configuring a controlled BS test receiver URL, deciding one test KYC record,
and confirming that its outbound event becomes `Delivered`. Failed events may
be retried only by System Manager or Riyansh KYC Approver.
