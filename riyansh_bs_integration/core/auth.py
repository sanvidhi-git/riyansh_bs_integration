from __future__ import annotations

import ipaddress

from riyansh_bs_integration.core.errors import AuthenticationError, PermissionDenied


def require_integration_access() -> None:
    import frappe

    user = frappe.session.user
    if not user or user == "Guest":
        raise AuthenticationError()

    roles = set(frappe.get_roles(user))
    if "Riyansh BS Integration" not in roles and "System Manager" not in roles:
        raise PermissionDenied("The user does not have the Riyansh BS Integration role")

    settings = frappe.get_cached_doc("BS Integration Settings")
    if not settings.integration_enabled or not settings.inbound_enabled:
        raise PermissionDenied("Inbound integration is disabled")

    allowed = [line.strip() for line in (settings.allowed_ips or "").splitlines() if line.strip()]
    if not allowed:
        return

    remote = frappe.local.request_ip
    try:
        address = ipaddress.ip_address(remote)
        permitted = any(
            address in ipaddress.ip_network(entry, strict=False)
            if "/" in entry
            else address == ipaddress.ip_address(entry)
            for entry in allowed
        )
    except ValueError as exc:
        raise PermissionDenied("The request IP address is invalid") from exc
    if not permitted:
        raise PermissionDenied("The request IP address is not allowed")
