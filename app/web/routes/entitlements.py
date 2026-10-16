"""Explicit demo, device proof, and audited administrator license operations."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import Field

from app.core.exceptions import ValidationError
from app.entitlements.domain import EntitlementError
from app.identity.domain import AccountView
from app.web.portal import Input, Portal, check_csrf, request_throttle, session_token
from app.web.responses import error_response

router = APIRouter(dependencies=[Depends(request_throttle)])
_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class Empty(Input):
    pass


class FlowReservation(Input):
    flow_id: str = Field(pattern=_UUID_PATTERN)


class Challenge(Input):
    public_key: str = Field(min_length=43, max_length=43)
    purpose: Literal["activate", "renew"]


class Proof(Input):
    challenge_id: str = Field(pattern=_UUID_PATTERN)
    signature: str = Field(min_length=86, max_length=86)


class Reason(Input):
    reason: str = Field(min_length=3, max_length=500)


class Grant(Reason):
    expires_at: str = Field(min_length=20, max_length=40)
    flow_limit: int = Field(ge=1, le=1000)


def customer(request: Request, portal: Portal) -> AccountView:
    token = (
        session_token(request, portal, "customer")
        if request.method == "GET"
        else check_csrf(request, portal, "customer")
    )
    return portal.identity.authenticate(token)


def administrator(request: Request, portal: Portal) -> AccountView:
    token = (
        session_token(request, portal, "admin")
        if request.method == "GET"
        else check_csrf(request, portal, "admin")
    )
    return portal.identity.authenticate(token, audience="admin")


@router.get("/api/trial")
def trial_status(request: Request, portal: Portal) -> dict[str, object]:
    return portal.trial.status(UUID(customer(request, portal).tenant_id))


@router.post("/api/trial/start")
def start_trial(body: Empty, request: Request, portal: Portal) -> dict[str, object]:
    return portal.trial.start(UUID(customer(request, portal).tenant_id))


@router.post("/api/trial/flows")
def reserve_flow(body: FlowReservation, request: Request, portal: Portal) -> dict[str, object]:
    return portal.trial.reserve_for_desktop(
        UUID(customer(request, portal).tenant_id), UUID(body.flow_id)
    )


@router.post("/api/license/challenge")
def challenge(body: Challenge, request: Request, portal: Portal) -> dict[str, object]:
    return portal.licenses.challenge(
        UUID(customer(request, portal).tenant_id), body.public_key, body.purpose
    )


@router.post("/api/license/activate")
def activate(body: Proof, request: Request, portal: Portal) -> dict[str, str]:
    return portal.licenses.activate(
        UUID(customer(request, portal).tenant_id), UUID(body.challenge_id), body.signature
    )


@router.post("/api/license/renew")
def renew(body: Proof, request: Request, portal: Portal) -> dict[str, str]:
    return portal.licenses.renew(
        UUID(customer(request, portal).tenant_id), UUID(body.challenge_id), body.signature
    )


@router.get("/api/admin/tenants/{tenant_id}/license")
def admin_status(tenant_id: UUID, request: Request, portal: Portal) -> dict[str, object]:
    administrator(request, portal)
    return portal.trial.status(tenant_id)


@router.post("/api/admin/tenants/{tenant_id}/license/grant")
def grant_license(
    tenant_id: UUID, body: Grant, request: Request, portal: Portal
) -> dict[str, object]:
    actor = administrator(request, portal)
    try:
        expires_at = datetime.fromisoformat(body.expires_at)
    except ValueError:
        raise ValidationError("License expiry must be an ISO timestamp") from None
    return portal.trial.grant(UUID(actor.id), tenant_id, expires_at, body.flow_limit, body.reason)


@router.post("/api/admin/tenants/{tenant_id}/license/revoke")
def revoke_license(
    tenant_id: UUID, body: Reason, request: Request, portal: Portal
) -> dict[str, object]:
    actor = administrator(request, portal)
    return portal.trial.revoke(UUID(actor.id), tenant_id, body.reason)


@router.post("/api/admin/tenants/{tenant_id}/device/reset")
def reset_device(
    tenant_id: UUID, body: Reason, request: Request, portal: Portal
) -> dict[str, object]:
    actor = administrator(request, portal)
    return portal.licenses.reset_device(UUID(actor.id), tenant_id, body.reason)


_ERRORS = {
    "TRIAL_NOT_STARTED": ("Çalıştırmadan önce demo sürümünü başlatın.", 403),
    "TRIAL_EXPIRED": ("Demo tamamlandı. Lisans için yöneticinizle iletişime geçin.", 403),
    "LICENSE_EXPIRED": ("Lisansınız sona erdi. Yöneticinizle iletişime geçin.", 403),
    "LICENSE_REVOKED": ("Lisansınız kapatıldı. Yöneticinizle iletişime geçin.", 403),
    "FLOW_LIMIT_REACHED": ("Akış sınırına ulaştınız. Mevcut akışlarınızı kullanabilirsiniz.", 409),
    "DEVICE_ALREADY_BOUND": ("Bu hesap başka bir bilgisayara bağlı.", 409),
    "DEVICE_NOT_BOUND": ("Bu bilgisayarı önce etkinleştirin.", 409),
    "DEVICE_RESET_PENDING": (
        "Önceki bilgisayarın lisansı kapanıyor. Bir dakika sonra deneyin.",
        409,
    ),
    "DEVICE_KEY_RETIRED": ("Bu cihaz anahtarı artık kullanılamıyor.", 403),
    "INVALID_DEVICE_CHALLENGE": ("Cihaz doğrulaması sona erdi. Yeniden deneyin.", 409),
    "INVALID_DEVICE_PROOF": ("Cihaz doğrulaması geçersiz.", 400),
    "TOO_MANY_CHALLENGES": ("Çok fazla doğrulama isteği var. Bir süre bekleyin.", 429),
    "ACCOUNT_NOT_FOUND": ("Hesap bulunamadı.", 404),
    "ACCOUNT_UNAVAILABLE": ("Hesap bulunamadı.", 404),
    "ACCOUNT_UNAPPROVED": ("E-posta doğrulaması ve yönetici onayı gerekiyor.", 403),
    "JOB_UNAVAILABLE": ("Çalıştırma bulunamadı.", 404),
    "ACCOUNT_SUSPENDED": ("Hesabınız askıya alınmış.", 403),
    "EMAIL_UNVERIFIED": ("E-posta adresinizi doğrulayın.", 403),
    "APPROVAL_REQUIRED": ("Yönetici onayı gerekiyor.", 403),
    "ADMIN_REQUIRED": ("Yönetici oturumu gerekiyor.", 403),
}


def error_response_for(error: EntitlementError) -> JSONResponse:
    message, status = _ERRORS.get(error.code, ("İstek alanlarını kontrol edin.", 422))
    return error_response(error.code, message, status)
