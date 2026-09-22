from __future__ import annotations

from riyansh_bs_integration.core.errors import IntegrationError


def success(data, message: str, correlation_id: str) -> dict:
    return {
        "success": True,
        "message": message,
        "data": data,
        "correlation_id": correlation_id,
    }


def failure(error: IntegrationError, correlation_id: str) -> dict:
    return {
        "success": False,
        "error": {
            "code": error.code,
            "message": error.message,
            "field": error.field,
            "details": error.details,
        },
        "correlation_id": correlation_id,
    }

