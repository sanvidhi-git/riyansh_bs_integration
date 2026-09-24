from __future__ import annotations

import functools
import json
import time
import uuid

from riyansh_bs_integration.core.errors import IntegrationError
from riyansh_bs_integration.core.responses import failure

SECRET_KEYS = {
    "api_key",
    "api_secret",
    "authorization",
    "bearer_token",
    "content",
    "file",
    "password",
    "signing_secret",
    "token",
}
PARTIAL_KEYS = {"aadhaar", "aadhaar_number", "account_number", "mobile", "pan_number"}


def _mask_tail(value, visible: int = 4) -> str:
    text = str(value or "")
    if len(text) <= visible:
        return "*" * len(text)
    return "*" * (len(text) - visible) + text[-visible:]


def redact(value, key: str | None = None):
    lowered = (key or "").lower()
    if lowered in SECRET_KEYS:
        return "[REDACTED]"
    if lowered in PARTIAL_KEYS:
        return _mask_tail(value)
    if isinstance(value, dict):
        return {item_key: redact(item_value, item_key) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def safe_payload(value):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return "[UNPARSEABLE REQUEST BODY]"
    return redact(value)


def new_correlation_id() -> str:
    return f"COR-{uuid.uuid4().hex.upper()}"


def write_api_log(**values):
    """Persist a masked log when running inside Frappe; remain importable in pure tests."""
    try:
        import frappe
    except ImportError:
        return None

    doc = frappe.new_doc("BS API Log")
    for field, value in values.items():
        safe_value = safe_payload(value) if field in {"request_body", "response_body"} else value
        setattr(doc, field, frappe.as_json(safe_value) if isinstance(safe_value, (dict, list)) else safe_value)
    doc.insert(ignore_permissions=True)
    return doc.name


def _write_api_log_safely(**values):
    """A logging failure must never replace the API's real response."""
    try:
        return write_api_log(**values)
    except Exception:
        try:
            import frappe

            frappe.log_error(title="Riyansh BS API log write failed")
        except Exception:
            pass
        return None


def integration_endpoint(interface: str, reference_key: str | None = None):
    """Wrap a whitelisted handler with access checks, envelopes and masked logging."""

    def decorator(function):
        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            from riyansh_bs_integration.core.auth import require_integration_access

            correlation_id = kwargs.pop("correlation_id", None) or new_correlation_id()
            started = time.monotonic()
            payload = kwargs.get("payload") or (args[0] if args else {}) or {}
            inspected_payload = safe_payload(payload)
            reference = inspected_payload.get(reference_key) if reference_key and isinstance(inspected_payload, dict) else None
            try:
                require_integration_access()
                result = function(*args, correlation_id=correlation_id, **kwargs)
                _write_api_log_safely(
                    interface=interface,
                    direction="Inbound",
                    correlation_id=correlation_id,
                    reference=reference,
                    request_body=inspected_payload,
                    response_body=result,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    status="Success",
                )
                return result
            except IntegrationError as exc:
                try:
                    import frappe

                    frappe.db.rollback()
                except (ImportError, AttributeError):
                    pass
                result = failure(exc, correlation_id)
                _write_api_log_safely(
                    interface=interface,
                    direction="Inbound",
                    correlation_id=correlation_id,
                    reference=reference,
                    request_body=inspected_payload,
                    response_body=result,
                    http_status=exc.http_status,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    status="Rejected",
                )
                try:
                    import frappe

                    frappe.local.response.http_status_code = exc.http_status
                except (ImportError, AttributeError):
                    pass
                return result
            except Exception:
                try:
                    import frappe

                    frappe.db.rollback()
                    frappe.log_error(title=f"{interface} unexpected integration error")
                    frappe.local.response.http_status_code = 500
                except (ImportError, AttributeError):
                    pass
                error = IntegrationError("INTERNAL_ERROR", "The request could not be processed", 500)
                result = failure(error, correlation_id)
                _write_api_log_safely(
                    interface=interface,
                    direction="Inbound",
                    correlation_id=correlation_id,
                    reference=reference,
                    request_body=inspected_payload,
                    response_body=result,
                    http_status=500,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    status="Error",
                )
                return result

        return wrapped

    return decorator
