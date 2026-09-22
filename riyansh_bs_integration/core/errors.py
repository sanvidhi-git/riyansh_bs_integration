from __future__ import annotations


class IntegrationError(Exception):
    """Expected integration failure safe to expose to an authenticated caller."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 422,
        *,
        field: str | None = None,
        details: list | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.field = field
        self.details = details or []


class ConflictError(IntegrationError):
    def __init__(self, code: str, message: str, *, field: str | None = None) -> None:
        super().__init__(code, message, 409, field=field)


class AuthenticationError(IntegrationError):
    def __init__(self, message: str = "Authentication is required") -> None:
        super().__init__("AUTHENTICATION_REQUIRED", message, 401)


class PermissionDenied(IntegrationError):
    def __init__(self, message: str = "Integration access is not permitted") -> None:
        super().__init__("PERMISSION_DENIED", message, 403)

