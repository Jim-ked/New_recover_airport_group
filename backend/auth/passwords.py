from __future__ import annotations

import base64
import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 310_000
SALT_BYTES = 16


class PasswordValidationError(ValueError):
    pass


def _validate_password(password: str) -> None:
    if not isinstance(password, str):
        raise PasswordValidationError("password must be a string")
    if len(password) < 8:
        raise PasswordValidationError("password must contain at least 8 characters")
    if len(password) > 1024:
        raise PasswordValidationError("password is too long")


def hash_password(password: str, *, iterations: int = DEFAULT_ITERATIONS, salt: bytes | None = None) -> str:
    _validate_password(password)
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations < 100_000:
        raise PasswordValidationError("PBKDF2 iterations must be at least 100000")
    if salt is None:
        salt = os.urandom(SALT_BYTES)
    if not isinstance(salt, (bytes, bytearray)) or len(salt) < 12:
        raise PasswordValidationError("password salt is invalid")
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes(salt), iterations)
    return "$".join((
        ALGORITHM,
        str(iterations),
        base64.urlsafe_b64encode(bytes(salt)).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    ))


def _decode_b64(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or not isinstance(encoded, str):
        return False
    try:
        algorithm, raw_iterations, raw_salt, raw_digest = encoded.split("$", 3)
        if algorithm != ALGORITHM:
            return False
        iterations = int(raw_iterations)
        if iterations < 100_000:
            return False
        salt = _decode_b64(raw_salt)
        expected = _decode_b64(raw_digest)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, base64.binascii.Error):
        return False


__all__ = ["hash_password", "verify_password", "PasswordValidationError", "DEFAULT_ITERATIONS"]
