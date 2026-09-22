# Test-site deployment

Deployment is a separate controlled action. Do not install on production.

1. Confirm the feature branch commit/checksum and review the source.
2. Back up the test-site database and private/public files.
3. Build a new custom image containing `riyansh_bs_integration`; do not change or remove `riyansh_integration`.
4. Run `bench --site test.riyansh.sanvidhierp.in install-app riyansh_bs_integration`.
5. Run `bench --site test.riyansh.sanvidhierp.in migrate`.
6. Assign `Riyansh BS Integration` only to the dedicated BS API System User.
7. Assign KYC roles to the correct internal users. The submitter and approver must be different users.
8. Configure **BS Integration Settings** with test Company/groups/territory/price list, warehouse-code mapping, tolerances, allowed BS IPs and BS KYC-result test URL.
9. Keep auto-submit for Sales Order and Credit Note disabled during UAT.
10. Run Postman smoke tests and check masked API logs/outbound events.

Rollback: disable Integration/Inbound/KYC Outbound switches first. Restore the
test backup or uninstall only this new app if approved. Never alter the old app
or production data as part of this rollback.
