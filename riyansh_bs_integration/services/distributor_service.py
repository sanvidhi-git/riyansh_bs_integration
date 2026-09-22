from __future__ import annotations

import json
import hashlib
from datetime import date, datetime

from riyansh_bs_integration.core.errors import ConflictError, IntegrationError
from riyansh_bs_integration.core.validation import (
    request_fingerprint,
    require,
    validate_aadhaar,
    validate_ifsc,
    validate_mobile,
    validate_pan,
    validate_pincode,
)

ALLOWED_DOCUMENT_TYPES = {"application/pdf", "image/jpeg", "image/png"}
REQUIRED_FILES = ("pan_card", "aadhaar_front", "aadhaar_back", "cancelled_cheque")


def _iso_date(value, field):
    try:
        return date.fromisoformat(str(value)).isoformat()
    except (TypeError, ValueError) as exc:
        raise IntegrationError("INVALID_DATE", f"{field} must use YYYY-MM-DD", 422, field=field) from exc


def _iso_datetime(value, field):
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.isoformat()
    except (TypeError, ValueError) as exc:
        raise IntegrationError("INVALID_DATETIME", f"{field} must be ISO-8601 with timezone", 422, field=field) from exc


def validate_distributor_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise IntegrationError("INVALID_JSON", "payload must be a JSON object", 400, field="payload")
    address = require(payload, "address")
    bank = require(payload, "bank")
    if not isinstance(address, dict) or not isinstance(bank, dict):
        raise IntegrationError("INVALID_OBJECT", "address and bank must be objects", 422)

    normalized = dict(payload)
    normalized["distributor_id"] = str(require(payload, "distributor_id")).strip()
    normalized["member_name"] = str(require(payload, "member_name")).strip()
    normalized["mobile"] = validate_mobile(require(payload, "mobile"))
    normalized["date_of_birth"] = _iso_date(require(payload, "date_of_birth"), "date_of_birth")
    normalized["pan_number"] = validate_pan(require(payload, "pan_number"))
    normalized["aadhaar_number"] = validate_aadhaar(require(payload, "aadhaar_number"))
    normalized["source_created_at"] = _iso_datetime(require(payload, "source_created_at"), "source_created_at")
    normalized["address"] = dict(address)
    for field in ("address_line_1", "city", "state", "country"):
        normalized["address"][field] = str(require(address, field)).strip()
    normalized["address"]["pincode"] = validate_pincode(require(address, "pincode"))
    normalized["bank"] = dict(bank)
    normalized["bank"]["bank_name"] = str(require(bank, "bank_name")).strip()
    normalized["bank"]["ifsc_code"] = validate_ifsc(require(bank, "ifsc_code"))
    normalized["bank"]["account_number"] = str(require(bank, "account_number")).strip()
    if not normalized["bank"]["account_number"].isdigit():
        raise IntegrationError("INVALID_ACCOUNT", "Account number must contain digits", 422, field="account_number")
    return normalized


def _detected_content_type(content: bytes) -> str | None:
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def validate_documents(files: dict, max_size_mb: int = 5) -> dict[str, str]:
    maximum = max_size_mb * 1024 * 1024
    digests = {}
    for key in REQUIRED_FILES:
        upload = files.get(key)
        if not upload:
            raise IntegrationError("MISSING_DOCUMENT", f"{key} is required", 422, field=key)
        content_type = (getattr(upload, "content_type", "") or "").lower()
        filename = (getattr(upload, "filename", "") or "").lower()
        content = upload.stream.read(maximum + 1)
        upload.stream.seek(0)
        if not content:
            raise IntegrationError("EMPTY_DOCUMENT", f"{key} is empty", 422, field=key)
        if len(content) > maximum:
            raise IntegrationError("DOCUMENT_TOO_LARGE", f"{key} exceeds {max_size_mb} MB", 413, field=key)
        if content_type not in ALLOWED_DOCUMENT_TYPES or not filename.endswith((".pdf", ".jpg", ".jpeg", ".png")):
            raise IntegrationError("UNSUPPORTED_DOCUMENT", f"{key} must be PDF, JPG or PNG", 415, field=key)
        detected = _detected_content_type(content)
        expected_extensions = {
            "application/pdf": (".pdf",),
            "image/png": (".png",),
            "image/jpeg": (".jpg", ".jpeg"),
        }
        if detected != content_type or not filename.endswith(expected_extensions.get(detected, ())):
            raise IntegrationError("DOCUMENT_TYPE_MISMATCH", f"{key} content does not match its name/type", 415, field=key)
        digests[key] = hashlib.sha256(content).hexdigest()
    return digests


def submit_distributor(payload: dict, files: dict, correlation_id: str) -> tuple[dict, bool]:
    import frappe
    from frappe.utils.file_manager import save_file

    normalized = validate_distributor_payload(payload)
    settings = frappe.get_cached_doc("BS Integration Settings")
    document_digests = validate_documents(files, int(settings.maximum_document_size_mb or 5))
    fingerprint = request_fingerprint({"payload": normalized, "documents": document_digests})
    existing_name = frappe.db.get_value("BS Distributor Onboarding", {"distributor_id": normalized["distributor_id"]}, "name")
    _validate_identity_uniqueness(frappe, normalized, existing_name)
    if existing_name:
        existing = frappe.get_doc("BS Distributor Onboarding", existing_name)
        if existing.request_fingerprint == fingerprint:
            return _onboarding_result(existing), False
        if existing.kyc_status == "Passed":
            existing.kyc_status = "Under Review"
            existing.flags.kyc_service_update = True
        _apply_payload(existing, normalized, correlation_id, fingerprint)
        _save_documents(existing, files, save_file)
        existing.save(ignore_permissions=True)
        return _onboarding_result(existing), False

    doc = frappe.new_doc("BS Distributor Onboarding")
    _apply_payload(doc, normalized, correlation_id, fingerprint)
    try:
        doc.insert(ignore_permissions=True)
    except frappe.UniqueValidationError:
        winner = frappe.db.get_value("BS Distributor Onboarding", {"distributor_id": normalized["distributor_id"]}, ["name", "request_fingerprint"], as_dict=True)
        if winner and winner.request_fingerprint == fingerprint:
            return _onboarding_result(frappe.get_doc("BS Distributor Onboarding", winner.name)), False
        raise ConflictError("DISTRIBUTOR_ID_CONFLICT", "Distributor ID already exists with different data", field="distributor_id")
    _save_documents(doc, files, save_file)
    doc.save(ignore_permissions=True)
    return _onboarding_result(doc), True


def _apply_payload(doc, payload, correlation_id, fingerprint):
    scalar_fields = ("distributor_id", "member_name", "enterprise_name", "mobile", "email", "date_of_birth", "nominee_name", "nominee_relationship", "pan_number", "source_created_at")
    for field in scalar_fields:
        doc.set(field, payload.get(field))
    doc.aadhaar_number = payload["aadhaar_number"]
    doc.aadhaar_hash = hashlib.sha256(payload["aadhaar_number"].encode()).hexdigest()
    doc.address_json = json.dumps(payload["address"], ensure_ascii=False)
    doc.bank_name = payload["bank"].get("bank_name")
    doc.branch_name = payload["bank"].get("branch_name")
    doc.ifsc_code = payload["bank"].get("ifsc_code")
    doc.account_number = payload["bank"].get("account_number")
    doc.account_hash = hashlib.sha256(payload["bank"]["account_number"].encode()).hexdigest()
    doc.correlation_id = correlation_id
    doc.request_fingerprint = fingerprint
    if not doc.kyc_status:
        doc.kyc_status = "Pending"


def _save_documents(doc, files, save_file):
    for fieldname in REQUIRED_FILES:
        upload = files[fieldname]
        saved = save_file(upload.filename, upload.stream.read(), doc.doctype, doc.name, is_private=1)
        doc.set(fieldname, saved.file_url)
        upload.stream.seek(0)


def _onboarding_result(doc):
    return {"distributor_id": doc.distributor_id, "erp_onboarding_id": doc.name, "kyc_status": doc.kyc_status.upper().replace(" ", "_")}


def _validate_identity_uniqueness(frappe, payload, current_name=None):
    checks = {
        "pan_number": payload["pan_number"],
        "mobile": payload["mobile"],
        "aadhaar_hash": hashlib.sha256(payload["aadhaar_number"].encode()).hexdigest(),
        "account_hash": hashlib.sha256(payload["bank"]["account_number"].encode()).hexdigest(),
    }
    for field, value in checks.items():
        match = frappe.db.get_value("BS Distributor Onboarding", {field: value}, "name")
        if match and match != current_name:
            raise ConflictError("DUPLICATE_IDENTITY", f"{field.replace('_hash', '').replace('_', ' ').title()} is already linked to another distributor", field=field.replace("_hash", ""))
