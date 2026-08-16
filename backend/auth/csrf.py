from __future__ import annotations

import secrets
from typing import Any


class CsrfValidationError(PermissionError):
    pass


def require_csrf_match(*, expected: Any, supplied: Any) -> None:
    """Require exact CSRF token equality without leaking token contents."""
    if not isinstance(expected, str) or not expected:
        raise CsrfValidationError("CSRF validation failed")
    if not isinstance(supplied, str) or not supplied:
        raise CsrfValidationError("CSRF validation failed")
    try:
        ok = secrets.compare_digest(supplied, expected)
    except TypeError:
        ok = False
    if not ok:
        raise CsrfValidationError("CSRF validation failed")


__all__ = ["CsrfValidationError", "require_csrf_match"]
