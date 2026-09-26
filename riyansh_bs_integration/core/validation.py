from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from riyansh_bs_integration.core.errors import IntegrationError

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
AADHAAR_RE = re.compile(r"^[0-9]{12}$")
IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
MOBILE_RE = re.compile(r"^[6-9][0-9]{9}$")
PINCODE_RE = re.compile(r"^[1-9][0-9]{5}$")


def to_database_datetime(value, system_timezone: str, field: str):
    """Convert an ISO-8601 instant to Frappe's timezone-naive DB datetime."""
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(ZoneInfo(system_timezone)).replace(tzinfo=None)
    except (TypeError, ValueError, KeyError) as exc:
        raise IntegrationError(
            "INVALID_DATETIME",
            f"{field} must be ISO-8601 with timezone",
            422,
            field=field,
        ) from exc


def request_fingerprint(payload) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def require(payload: dict, field: str):
    value = payload.get(field)
    if value is None or value == "":
        raise IntegrationError("REQUIRED_FIELD", f"{field} is required", 422, field=field)
    return value


def validate_pan(value: str) -> str:
    normalized = (value or "").replace(" ", "").upper()
    if not PAN_RE.fullmatch(normalized):
        raise IntegrationError("INVALID_PAN", "PAN number format is invalid", 422, field="pan_number")
    return normalized


def validate_aadhaar(value: str) -> str:
    normalized = re.sub(r"[ -]", "", value or "")
    if not AADHAAR_RE.fullmatch(normalized):
        raise IntegrationError(
            "INVALID_AADHAAR", "Aadhaar number must contain 12 digits", 422, field="aadhaar_number"
        )
    return normalized


def validate_ifsc(value: str) -> str:
    normalized = (value or "").replace(" ", "").upper()
    if not IFSC_RE.fullmatch(normalized):
        raise IntegrationError("INVALID_IFSC", "IFSC code format is invalid", 422, field="ifsc_code")
    return normalized


def validate_mobile(value: str) -> str:
    normalized = re.sub(r"\D", "", value or "")
    if normalized.startswith("91") and len(normalized) == 12:
        normalized = normalized[2:]
    if not MOBILE_RE.fullmatch(normalized):
        raise IntegrationError("INVALID_MOBILE", "Mobile number format is invalid", 422, field="mobile")
    return normalized


def validate_pincode(value: str) -> str:
    normalized = str(value or "").strip()
    if not PINCODE_RE.fullmatch(normalized):
        raise IntegrationError("INVALID_PINCODE", "Pincode must contain six digits", 422, field="pincode")
    return normalized

