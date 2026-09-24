from __future__ import annotations

import json
import hashlib
from datetime import timedelta
from zoneinfo import ZoneInfo

from riyansh_bs_integration.core.errors import IntegrationError, PermissionDenied
from riyansh_bs_integration.core.logging import safe_payload
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
    serialized_payload = json.dumps(payload, separators=(",", ":"), default=str)
    event_key = hashlib.sha256(
        f"KYC_RESULT\n{onboarding.name}\n{serialized_payload}".encode("utf-8")
    ).hexdigest()
    existing = frappe.db.get_value("BS Outbound Event", {"event_key": event_key}, "name")
    if existing:
        return existing
    event = frappe.get_doc({
        "doctype": "BS Outbound Event",
        "event_type": "KYC_RESULT",
        "onboarding": onboarding.name,
        "target_url": settings.kyc_result_url,
        "payload": serialized_payload,
        "event_key": event_key,
        "status": "Queued",
        "correlation_id": onboarding.correlation_id,
    })
    try:
        event.insert(ignore_permissions=True)
    except frappe.UniqueValidationError:
        return frappe.db.get_value("BS Outbound Event", {"event_key": event_key}, "name")
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

    if not _claim_event(event_name):
        return frappe.db.get_value("BS Outbound Event", event_name, "status")
    event = frappe.get_doc("BS Outbound Event", event_name)
    settings = frappe.get_cached_doc("BS Integration Settings")
    maximum_attempts = int(settings.maximum_outbound_attempts or 5)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    headers["Idempotency-Key"] = event.event_key
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
        event.response_body = _safe_response_body(response.text)
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


def _claim_event(event_name: str) -> bool:
    """Claim one event under a row lock so only one worker can send it."""
    import frappe
    from frappe.utils import now_datetime

    rows = frappe.db.sql(
        """select status, attempt_count
           from `tabBS Outbound Event`
           where name = %s for update""",
        (event_name,),
        as_dict=True,
    )
    if not rows or rows[0].status not in {"Queued", "Retrying"}:
        return False
    frappe.db.set_value(
        "BS Outbound Event",
        event_name,
        {
            "status": "Processing",
            "attempt_count": int(rows[0].attempt_count or 0) + 1,
            "last_attempt_at": now_datetime(),
        },
        update_modified=False,
    )
    # Release the row lock before doing network I/O. The durable Processing
    # state prevents another worker from sending the same event concurrently.
    frappe.db.commit()
    return True


def _safe_response_body(value) -> str:
    text = value or ""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return "[NON-JSON RESPONSE]" if text else ""
    return json.dumps(safe_payload(parsed), ensure_ascii=False)[:2000]


def process_due_events():
    import frappe
    from frappe.utils import add_to_date, now_datetime

    # Recover a worker that died after claiming an event. The same stable
    # Idempotency-Key is reused, allowing the BS receiver to deduplicate it.
    stale_names = frappe.get_all(
        "BS Outbound Event",
        filters={
            "status": "Processing",
            "last_attempt_at": ["<=", add_to_date(now_datetime(), minutes=-10)],
        },
        pluck="name",
        limit_page_length=100,
    )
    for name in stale_names:
        frappe.db.set_value(
            "BS Outbound Event",
            name,
            {"status": "Retrying", "next_attempt_at": now_datetime()},
            update_modified=False,
        )

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
