from __future__ import annotations

import json
from datetime import timedelta
from zoneinfo import ZoneInfo

from riyansh_bs_integration.core.errors import IntegrationError, PermissionDenied
from riyansh_bs_integration.services.kyc_service import build_kyc_payload

BACKOFF_SECONDS = (60, 300, 900, 3600)


def retry_delay(attempt_number: int) -> int:
    index = max(0, min(int(attempt_number) - 1, len(BACKOFF_SECONDS) - 1))
    return BACKOFF_SECONDS[index]


def classify_response(status_code: int) -> str:
    if 200 <= status_code < 300:
        return "delivered"
    if status_code == 429 or status_code >= 500:
        return "retry"
    return "failed"


def queue_kyc_result(onboarding_name: str):
    import frappe

    onboarding = frappe.get_doc("BS Distributor Onboarding", onboarding_name)
    settings = frappe.get_cached_doc("BS Integration Settings")
    if not settings.integration_enabled or not settings.kyc_outbound_enabled:
        raise IntegrationError("OUTBOUND_DISABLED", "KYC outbound integration is disabled", 503)
    if not settings.kyc_result_url or not str(settings.kyc_result_url).startswith("https://"):
        raise IntegrationError("INVALID_OUTBOUND_URL", "A HTTPS KYC result URL is required", 422)
    status = "PASS" if onboarding.kyc_status == "Passed" else "FAIL"
    payload = build_kyc_payload(
        distributor_id=onboarding.distributor_id,
        status=status,
        verified_at=_iso_system_datetime(onboarding.verified_at),
        customer=onboarding.customer,
        supplier=onboarding.supplier,
        failure_reason_code=onboarding.failure_reason_code,
        failure_reason=onboarding.failure_reason,
    )
    existing = frappe.db.get_value(
        "BS Outbound Event",
        {"event_type": "KYC_RESULT", "onboarding": onboarding.name},
        "name",
    )
    if existing:
        return existing
    event = frappe.get_doc({
        "doctype": "BS Outbound Event",
        "event_type": "KYC_RESULT",
        "onboarding": onboarding.name,
        "target_url": settings.kyc_result_url,
        "payload": json.dumps(payload, separators=(",", ":"), default=str),
        "status": "Queued",
        "correlation_id": onboarding.correlation_id,
    })
    event.insert(ignore_permissions=True)
    frappe.enqueue(
        "riyansh_bs_integration.core.outbound.dispatch_event",
        event_name=event.name,
        enqueue_after_commit=True,
    )
    return event.name


def _iso_system_datetime(value):
    from frappe.utils import get_datetime, get_system_timezone

    parsed = get_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(get_system_timezone()))
    return parsed.isoformat()


def dispatch_event(event_name: str, http_post=None):
    import frappe
    import requests
    from frappe.utils import now_datetime

    event = frappe.get_doc("BS Outbound Event", event_name)
    if event.status == "Delivered":
        return "Delivered"
    settings = frappe.get_cached_doc("BS Integration Settings")
    maximum_attempts = int(settings.maximum_outbound_attempts or 5)
    event.attempt_count = int(event.attempt_count or 0) + 1
    event.last_attempt_at = now_datetime()
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    authentication_type = settings.authentication_type
    if authentication_type == "Bearer Token":
        headers["Authorization"] = f"Bearer {settings.get_password('bearer_token')}"
    elif authentication_type == "API Key and Secret":
        headers["Authorization"] = f"token {settings.get_password('api_key')}:{settings.get_password('api_secret')}"
    post = http_post or requests.post
    try:
        response = post(
            event.target_url,
            json=event.payload if isinstance(event.payload, dict) else json.loads(event.payload),
            headers=headers,
            timeout=int(settings.request_timeout_seconds or 30),
        )
        outcome = classify_response(response.status_code)
        event.response_status = response.status_code
        event.response_body = (response.text or "")[:2000]
        error_message = None
    except requests.RequestException as exc:
        outcome = "retry"
        error_message = str(exc)[:500]

    if outcome == "delivered":
        event.status = "Delivered"
        event.delivered_at = now_datetime()
        frappe.db.set_value("BS Distributor Onboarding", event.onboarding, "outbound_status", "Delivered")
    elif outcome == "retry" and event.attempt_count < maximum_attempts:
        event.status = "Retrying"
        event.next_attempt_at = now_datetime() + timedelta(seconds=retry_delay(event.attempt_count))
        event.error_message = error_message
    else:
        event.status = "Failed"
        event.error_message = error_message or f"HTTP {event.response_status}"
        frappe.db.set_value("BS Distributor Onboarding", event.onboarding, "outbound_status", "Failed")
    event.save(ignore_permissions=True)
    return event.status


def process_due_events():
    import frappe
    from frappe.utils import now_datetime

    names = frappe.get_all(
        "BS Outbound Event",
        filters={"status": ["in", ["Queued", "Retrying"]]},
        or_filters=[
            ["next_attempt_at", "is", "not set"],
            ["next_attempt_at", "<=", now_datetime()],
        ],
        pluck="name",
        limit_page_length=100,
    )
    for name in names:
        frappe.enqueue("riyansh_bs_integration.core.outbound.dispatch_event", event_name=name)


def retry_event(event_name: str):
    import frappe

    if not set(frappe.get_roles(frappe.session.user)).intersection({"System Manager", "Riyansh KYC Approver"}):
        raise PermissionDenied("Manual retry requires System Manager or Riyansh KYC Approver")
    event = frappe.get_doc("BS Outbound Event", event_name)
    if event.status != "Failed":
        raise IntegrationError("EVENT_NOT_FAILED", "Only failed events can be retried", 422)
    event.status = "Queued"
    event.next_attempt_at = None
    event.error_message = None
    event.save(ignore_permissions=True)
    frappe.enqueue("riyansh_bs_integration.core.outbound.dispatch_event", event_name=event.name, enqueue_after_commit=True)
    return event.name
