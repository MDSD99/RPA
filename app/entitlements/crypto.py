"""Ed25519 protocol: unpadded base64url and exact canonical JSON message strings."""

import base64
import hashlib
import json
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from app.entitlements.domain import EntitlementError


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def decode(value: str, size: int) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value) or len(value) > 128:
        raise EntitlementError("INVALID_DEVICE_PROOF")
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except ValueError:
        raise EntitlementError("INVALID_DEVICE_PROOF") from None
    if len(raw) != size or encode(raw) != value:
        raise EntitlementError("INVALID_DEVICE_PROOF")
    return raw


def thumbprint(public_key: str) -> str:
    return hashlib.sha256(decode(public_key, 32)).hexdigest()


def canonical(value: dict[str, object]) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def verify(public_key: str, signature: str, message: str) -> None:
    key = Ed25519PublicKey.from_public_bytes(decode(public_key, 32))
    try:
        key.verify(decode(signature, 64), message.encode("utf-8"))
    except (ValueError, InvalidSignature):
        raise EntitlementError("INVALID_DEVICE_PROOF") from None


class LeaseSigner:
    def __init__(self, private_key: str) -> None:
        self._key = Ed25519PrivateKey.from_private_bytes(decode(private_key, 32))
        self.public_key = encode(
            self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )

    def sign(self, payload: dict[str, object]) -> dict[str, str]:
        message = canonical(payload)
        return {
            "payload": message,
            "signature": encode(self._key.sign(message.encode("utf-8"))),
            "public_key": self.public_key,
        }
