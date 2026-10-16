"""Bounded credentials, one-way token digests and encrypted delivery payloads."""

import hashlib
import hmac
import json
import secrets
import unicodedata
from datetime import datetime

import pyotp
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError
from cryptography.fernet import Fernet, InvalidToken
from email_validator import EmailNotValidError, validate_email

from app.identity.domain import AuthError

_PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, type=Type.ID)
_DUMMY_HASH = _PASSWORDS.hash("MultiRPA absent-account timing placeholder")


def normalize_email(value: str) -> str:
    if not isinstance(value, str) or len(value) > 320:
        raise AuthError("INVALID_EMAIL")
    try:
        result = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError:
        raise AuthError("INVALID_EMAIL") from None
    # Explicit product policy: email identifiers are case insensitive.
    return result.normalized.lower()


def validate_password(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 15 <= len(value) <= 128
        or any(unicodedata.category(char).startswith("C") for char in value)
        or len(value.encode("utf-8")) > 512
    ):
        raise AuthError("INVALID_PASSWORD")
    return value


def hash_password(value: str) -> str:
    return _PASSWORDS.hash(validate_password(value))


def password_matches(value: str, encoded: str | None) -> bool:
    if (
        not isinstance(value, str)
        or len(value) > 128
        or any(unicodedata.category(char).startswith("C") for char in value)
        or len(value.encode("utf-8")) > 512
    ):
        return False
    try:
        valid = _PASSWORDS.verify(encoded or _DUMMY_HASH, value)
        return valid and encoded is not None
    except (VerificationError, InvalidHashError):
        return False


def validate_name(value: str) -> str:
    if not isinstance(value, str):
        raise AuthError("INVALID_DISPLAY_NAME")
    value = value.strip()
    if not 1 <= len(value) <= 120 or any(
        unicodedata.category(char).startswith("C") for char in value
    ):
        raise AuthError("INVALID_DISPLAY_NAME")
    return value


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_digest(value: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 43
        or not all(char.isascii() and (char.isalnum() or char in "-_") for char in value)
    ):
        raise AuthError("INVALID_TOKEN")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def encrypt_payload(cipher: Fernet, payload: dict[str, str]) -> str:
    return cipher.encrypt(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")


def validate_totp_secret(value: str) -> str:
    if not isinstance(value, str):
        raise AuthError("INVALID_TOTP_SECRET")
    value = value.strip().upper()
    if not 32 <= len(value) <= 128 or any(
        char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567" for char in value
    ):
        raise AuthError("INVALID_TOTP_SECRET")
    try:
        pyotp.TOTP(value).at(0)
    except (ValueError, TypeError):
        raise AuthError("INVALID_TOTP_SECRET") from None
    return value


def accepted_totp_counter(
    cipher: Fernet, encrypted: str, code: str | None, now: datetime, last_counter: int | None
) -> int:
    if not code:
        raise AuthError("MFA_REQUIRED")
    if not isinstance(code, str) or len(code) != 6 or not code.isascii() or not code.isdigit():
        raise AuthError("MFA_INVALID")
    try:
        secret = cipher.decrypt(encrypted.encode("ascii")).decode("ascii")
        generator = pyotp.TOTP(secret)
        current = int(now.timestamp()) // generator.interval
        for counter in (current, current - 1, current + 1):
            if (last_counter is None or counter > last_counter) and hmac.compare_digest(
                generator.at(counter * generator.interval), code
            ):
                return counter
    except (InvalidToken, ValueError, UnicodeError):
        pass
    raise AuthError("MFA_INVALID")
