"""Pinned, device-bound execution permits. A local clock never extends a permit."""

import base64
import hashlib
import json
import math
import threading
import time
from collections.abc import Callable
from typing import TypedDict, cast
from uuid import UUID

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from app.core.exceptions import OwnershipLostError


class LicenseDenied(OwnershipLostError):
    def __init__(self, code: str = "DESKTOP_LICENSE_REQUIRED") -> None:
        super().__init__(code)
        self.code = code


class Permit(TypedDict):
    version: int
    issuer: str
    tenant_id: str
    key_thumbprint: str
    device_id: str
    lease_id: str
    issued_at: int
    expires_at: int
    entitlement_expires_at: int
    valid_for_ms: int
    flow_ids: list[str]


def decode_key(value: str, length: int) -> bytes:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        canonical = base64.urlsafe_b64encode(decoded).decode().rstrip("=")
        if len(decoded) != length or canonical != value:
            raise ValueError
        return decoded
    except (ValueError, TypeError):
        raise LicenseDenied("DESKTOP_SIGNATURE_INVALID") from None


class LeaseGuard:
    """Revalidate before every effect, on resume, and after each bounded wait.

    Sleep/resume explicitly invalidates the permit in main. Independent wall and
    monotonic clocks additionally reject expiry, clock rollback and long pauses.
    Administrator control of the OS/binary is outside this software boundary.
    """

    def __init__(
        self,
        server_key: str,
        device_key: str,
        tenant_id: str,
        *,
        wall: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._key = Ed25519PublicKey.from_public_bytes(decode_key(server_key, 32))
        self._thumbprint = hashlib.sha256(decode_key(device_key, 32)).hexdigest()
        self.tenant_id = str(UUID(tenant_id))
        self._wall = wall
        self._monotonic = monotonic
        self._lock = threading.RLock()
        self._permit: Permit | None = None
        self._wall_start = 0.0
        self._mono_start = 0.0
        self._deadline = 0.0
        self._latest_issue = 0
        self._seen_leases: set[str] = set()

    def accept(self, envelope: dict[str, str], *, elapsed_ms: float = 0) -> None:
        try:
            payload = envelope["payload"]
            if not isinstance(payload, str) or len(payload.encode("utf-8")) > 65_536:
                raise ValueError
            signature = decode_key(envelope["signature"], 64)
            self._key.verify(signature, payload.encode("utf-8"))
            # Never parse or trust unsigned payload or response.public_key.
            permit = json.loads(payload)
            issued, expires, end = (
                permit["issued_at"],
                permit["expires_at"],
                permit["entitlement_expires_at"],
            )
            if any(type(value) is not int for value in (issued, expires, end)):
                raise ValueError
            flows = permit["flow_ids"]
            valid_for = permit["valid_for_ms"]
            if (
                type(permit["version"]) is not int
                or permit["version"] != 1
                or permit["issuer"] != "multirpa"
                or permit["tenant_id"] != self.tenant_id
                or permit["key_thumbprint"] != self._thumbprint
                or not isinstance(flows, list)
                or len(flows) > 1000
                or len(set(flows)) != len(flows)
                or not issued < expires <= min(issued + 60, end)
                or type(valid_for) is not int
                or not 0 < valid_for <= 60_000
                or type(elapsed_ms) not in (int, float)
                or not math.isfinite(elapsed_ms)
                or not 0 <= elapsed_ms < valid_for
            ):
                raise ValueError
            for value in [permit["device_id"], permit["lease_id"], *flows]:
                if not isinstance(value, str):
                    raise ValueError
                UUID(value)
            with self._lock:
                now = self._wall()
                if (
                    issued < self._latest_issue
                    or not issued - 5 <= now < expires
                    or permit["lease_id"] in self._seen_leases
                ):
                    raise ValueError
                self._seen_leases.add(permit["lease_id"])
                self._latest_issue = issued
                self._wall_start, self._mono_start = now, self._monotonic()
                self._deadline = self._mono_start + min(
                    expires - now, (valid_for - elapsed_ms) / 1000
                )
                self._permit = cast(Permit, permit)
        except (KeyError, TypeError, ValueError, InvalidSignature, LicenseDenied):
            self.invalidate()
            raise LicenseDenied("DESKTOP_LICENSE_INVALID") from None

    def invalidate(self) -> None:
        with self._lock:
            self._permit = None

    def check(self, flow_id: str) -> None:
        with self._lock:
            if self._permit is None:
                raise LicenseDenied()
            wall, mono = self._wall(), self._monotonic()
            elapsed, wall_elapsed = mono - self._mono_start, wall - self._wall_start
            if (
                not math.isfinite(wall)
                or not math.isfinite(mono)
                or elapsed < 0
                or wall_elapsed < -1
                or abs(wall_elapsed - elapsed) > 5
                or mono >= self._deadline
                or wall >= self._permit["expires_at"]
            ):
                self._permit = None
                raise LicenseDenied("DESKTOP_LICENSE_EXPIRED")
            if flow_id not in self._permit["flow_ids"]:
                raise LicenseDenied("DESKTOP_FLOW_NOT_RESERVED")
