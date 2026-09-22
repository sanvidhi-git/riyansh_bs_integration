# Riyansh BS Integration

Standalone Frappe v16 custom app for four approved Riyansh Business Software flows:

1. Distributor and KYC submission from BS to ERPNext.
2. KYC result delivery from ERPNext to BS.
3. Confirmed order submission from BS to ERPNext.
4. Approved credit-note submission from BS to ERPNext.

This app does not modify or depend on the legacy `riyansh_integration` app.

## Endpoints

| Flow | Caller | Endpoint |
|---|---|---|
| Distributor/KYC intake | BS | `POST /api/method/riyansh_bs_integration.api.v1.distributor.submit` |
| KYC result | ERPNext | BS URL configured in **BS Integration Settings** |
| Confirmed order | BS | `POST /api/method/riyansh_bs_integration.api.v1.order.create` |
| Approved credit note | BS | `POST /api/method/riyansh_bs_integration.api.v1.credit_note.create` |

Inbound calls require a Frappe token belonging to a System User with the
`Riyansh BS Integration` role. Guest access is not enabled. Sales Orders and
return Sales Invoices remain Draft unless their separate auto-submit settings
are deliberately enabled after UAT.

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/TESTING.md](docs/TESTING.md).
