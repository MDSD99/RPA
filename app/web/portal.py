"""Customer identity and composition of tenant-scoped automation services."""

import hashlib
import hmac
import os
from collections.abc import AsyncIterator
from contextlib import ExitStack, asynccontextmanager
from dataclasses import asdict, dataclass
from datetime import UTC
from html import escape
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.branding import PRODUCT_NAME
from app.core.config import ConfigurationError, DatabaseSettings
from app.core.portal_config import PortalSettings
from app.database.connection import create_db_engine, create_session_factory
from app.database.schema import schema_is_current
from app.entitlements.devices import DeviceLicenseService
from app.entitlements.domain import EntitlementError
from app.entitlements.service import TrialService
from app.identity.domain import Audience, AuthError
from app.identity.security import normalize_email
from app.identity.service import IdentityService
from app.secrets.service import SecretService
from app.services.download_service import DownloadService
from app.services.rate_limit_service import RateLimitService
from app.web.errors import register_error_handlers
from app.web.middleware import LocalRequestBoundary
from app.web.portal_runtime import build_customer_runtime
from app.web.responses import error_response

_WEB = Path(__file__).resolve().parent
_GENERIC_MAIL = "Bilgiler uygunsa e-posta gönderim sırasına alındı. Gelen kutunuzu kontrol edin."


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EmailInput(Input):
    email: str = Field(min_length=3, max_length=320)


class RegisterInput(EmailInput):
    password: str = Field(min_length=15, max_length=128)
    display_name: str = Field(min_length=1, max_length=120)


class LoginInput(EmailInput):
    password: str = Field(min_length=1, max_length=128)


class AdminLoginInput(LoginInput):
    totp: str = Field(pattern=r"^\d{6}$")


class TokenInput(Input):
    token: str = Field(min_length=20, max_length=256)


class ResetInput(TokenInput):
    password: str = Field(min_length=15, max_length=128)


class DecisionInput(Input):
    decision: Literal["approve", "reject", "suspend", "restore"]
    reason: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True)
class PortalServices:
    identity: IdentityService
    limiter: RateLimitService
    settings: PortalSettings
    trial: TrialService
    licenses: DeviceLicenseService
    secrets: SecretService
    downloads: DownloadService

    def cookie_name(self, audience: Audience) -> str:
        prefix = "__Host-" if self.settings.secure_cookie else ""
        return f"{prefix}multirpa_{audience}"

    def csrf(self, token: str, audience: Audience) -> str:
        return hmac.new(
            self.settings.secret_key.encode("ascii"),
            f"csrf\0{audience}\0{token}".encode(),
            hashlib.sha256,
        ).hexdigest()


def services(request: Request) -> PortalServices:
    return request.app.state.portal


Portal = Annotated[PortalServices, Depends(services)]


def session_token(request: Request, portal: PortalServices, audience: Audience) -> str:
    value = request.cookies.get(portal.cookie_name(audience), "")
    if len(value) > 256 or not value.isascii():
        raise AuthError("SESSION_EXPIRED")
    return value


def check_csrf(request: Request, portal: PortalServices, audience: Audience) -> str:
    token = session_token(request, portal, audience)
    supplied = request.headers.get("x-csrf-token", "")
    if (
        not token
        or not supplied.isascii()
        or not hmac.compare_digest(portal.csrf(token, audience), supplied)
    ):
        raise AuthError("CSRF_REJECTED")
    return token


def _throttle(portal: PortalServices, scope: str, identity: str, limit: int, seconds: int) -> None:
    if not portal.limiter.allow(scope, identity, limit=limit, seconds=seconds):
        raise AuthError("RATE_LIMITED")


def _email_throttle(portal: PortalServices, scope: str, email: str, limit: int) -> None:
    _throttle(portal, scope, normalize_email(email), limit, 900)


def request_throttle(request: Request, portal: Portal) -> None:
    # Uvicorn must trust forwarded headers only from an explicitly configured proxy.
    # Application code does not accept a caller-controlled X-Forwarded-For header.
    peer = request.client.host if request.client else "unknown"
    _throttle(portal, "request", peer, 120, 60)
    if request.method == "POST" and request.url.path.endswith(
        ("/login", "/register", "/password/reset")
    ):
        _throttle(portal, "credentials", peer, 30, 900)


def _user_response(portal: PortalServices, token: str, audience: Audience) -> dict[str, object]:
    return {
        "user": asdict(portal.identity.authenticate(token, audience=audience)),
        "csrf_token": portal.csrf(token, audience),
    }


def _login(
    data: LoginInput,
    request: Request,
    response: Response,
    portal: PortalServices,
    audience: Audience,
) -> dict[str, object]:
    _email_throttle(portal, "login", data.email, 10)
    result = portal.identity.login(
        data.email, data.password, audience=audience, totp=getattr(data, "totp", None)
    )
    previous = session_token(request, portal, audience)
    if previous:
        portal.identity.logout(previous, audience=audience)
    response.set_cookie(
        portal.cookie_name(audience),
        result.token,
        httponly=True,
        secure=portal.settings.secure_cookie,
        samesite="strict",
        path="/",
        expires=result.expires_at.astimezone(UTC),
    )
    return {"user": asdict(result.user), "csrf_token": portal.csrf(result.token, audience)}


def _logout(
    request: Request, response: Response, portal: PortalServices, audience: Audience
) -> dict[str, object]:
    token = check_csrf(request, portal, audience)
    portal.identity.logout(token, audience=audience)
    response.delete_cookie(
        portal.cookie_name(audience),
        path="/",
        secure=portal.settings.secure_cookie,
        httponly=True,
        samesite="strict",
    )
    return {"message": "Oturum kapatıldı."}


router = APIRouter(dependencies=[Depends(request_throttle)])


@router.get("/api/auth/me")
def me(request: Request, portal: Portal) -> dict[str, object]:
    return _user_response(portal, session_token(request, portal, "customer"), "customer")


@router.post("/api/auth/register", status_code=202)
def register(data: RegisterInput, portal: Portal) -> dict[str, object]:
    _email_throttle(portal, "mail", data.email, 5)
    portal.identity.register(data.email, data.password, data.display_name)
    return {"message": _GENERIC_MAIL}


@router.post("/api/auth/login")
def login(
    data: LoginInput, request: Request, response: Response, portal: Portal
) -> dict[str, object]:
    return _login(data, request, response, portal, "customer")


@router.post("/api/auth/logout")
def logout(request: Request, response: Response, portal: Portal) -> dict[str, object]:
    return _logout(request, response, portal, "customer")


@router.post("/api/auth/verification/request", status_code=202)
def request_verification(data: EmailInput, portal: Portal) -> dict[str, object]:
    _email_throttle(portal, "mail", data.email, 5)
    portal.identity.request_verification(data.email)
    return {"message": _GENERIC_MAIL}


@router.post("/api/auth/verification/confirm")
def confirm(data: TokenInput, portal: Portal) -> dict[str, object]:
    portal.identity.verify_email(data.token)
    return {
        "message": "E-posta adresiniz doğrulandı. Yönetici onayı sonrasında devam edebilirsiniz."
    }


@router.post("/api/auth/password/request", status_code=202)
def request_password(data: EmailInput, portal: Portal) -> dict[str, object]:
    _email_throttle(portal, "mail", data.email, 5)
    portal.identity.request_password_reset(data.email)
    return {"message": _GENERIC_MAIL}


@router.post("/api/auth/password/reset")
def reset_password(data: ResetInput, portal: Portal) -> dict[str, object]:
    portal.identity.reset_password(data.token, data.password)
    return {"message": "Parolanız değiştirildi ve mevcut oturumlarınız kapatıldı."}


@router.post("/api/admin/login")
def admin_login(
    data: AdminLoginInput, request: Request, response: Response, portal: Portal
) -> dict[str, object]:
    return _login(data, request, response, portal, "admin")


@router.get("/api/admin/me")
def admin_me(request: Request, portal: Portal) -> dict[str, object]:
    return _user_response(portal, session_token(request, portal, "admin"), "admin")


@router.post("/api/admin/logout")
def admin_logout(request: Request, response: Response, portal: Portal) -> dict[str, object]:
    return _logout(request, response, portal, "admin")


@router.get("/api/admin/accounts")
def accounts(
    request: Request, portal: Portal, limit: int = 50, offset: int = 0
) -> dict[str, object]:
    return portal.identity.list_accounts(
        session_token(request, portal, "admin"), limit=limit, offset=offset
    )


@router.post("/api/admin/accounts/{user_id}/decision")
def decide(
    user_id: str, data: DecisionInput, request: Request, portal: Portal
) -> dict[str, object]:
    token = check_csrf(request, portal, "admin")
    user = portal.identity.decide(token, user_id, decision=data.decision, reason=data.reason)
    return {"user": asdict(user)}


_AUTH_MESSAGES = {
    "INVALID_CREDENTIALS": ("E-posta, parola veya doğrulama kodu geçersiz.", 401),
    "SESSION_EXPIRED": ("Oturumunuz sona erdi. Yeniden giriş yapın.", 401),
    "INVALID_TOKEN": ("Bağlantı geçersiz veya süresi dolmuş. Yeni bağlantı isteyin.", 400),
    "EMAIL_UNVERIFIED": ("Önce e-posta adresinizi doğrulayın.", 403),
    "APPROVAL_REQUIRED": ("Hesabınız için yönetici onayı gerekiyor.", 403),
    "ACCOUNT_SUSPENDED": ("Hesabınız askıya alınmış. Yöneticinizle iletişime geçin.", 403),
    "ADMIN_REQUIRED": ("Bu işlem için yönetici oturumu gerekiyor.", 403),
    "MFA_REQUIRED": ("Doğrulayıcı uygulamanızdaki altı haneli kodu girin.", 401),
    "MFA_INVALID": ("E-posta, parola veya doğrulama kodu geçersiz.", 401),
    "ACCOUNT_NOT_FOUND": ("Hesap bulunamadı.", 404),
    "INVALID_DECISION": ("Bu karar hesabın mevcut durumuna uygulanamaz.", 409),
    "INVALID_PASSWORD": ("Parolanız 15–128 karakter olmalı.", 422),
    "INVALID_EMAIL": ("Geçerli bir e-posta adresi girin.", 422),
    "INVALID_DISPLAY_NAME": ("Ad alanını kontrol edin.", 422),
    "INVALID_REASON": ("Kararın gerekçesini yazın.", 422),
    "SELF_ACTION_FORBIDDEN": ("Kendi yönetici hesabınızda bu işlemi yapamazsınız.", 403),
    "ADMIN_ACTION_FORBIDDEN": ("Yönetici hesapları bu ekrandan değiştirilemez.", 403),
    "CSRF_REJECTED": ("Sayfayı yenileyip tekrar deneyin.", 403),
    "RATE_LIMITED": ("Çok sayıda deneme yapıldı. Bir süre bekleyip yeniden deneyin.", 429),
}


def create_portal_app(
    session_factory: sessionmaker[Session] | None = None,
    settings: PortalSettings | None = None,
    *,
    start_workers: bool = False,
    external_workers: bool = False,
) -> FastAPI:
    config = settings or PortalSettings.from_env()
    if os.environ.get("APP_ENV") == "production" and config.app_env != "production":
        raise ConfigurationError("Production process requires production portal settings")
    if config.app_env == "production" and start_workers:
        raise ConfigurationError("Production API requires a separate execution worker process")
    if type(start_workers) is not bool or type(external_workers) is not bool:
        raise ConfigurationError("Worker configuration must use boolean flags")

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        with ExitStack() as resources:
            factory = session_factory
            if factory is None:
                engine = create_db_engine(DatabaseSettings.from_env())
                resources.callback(engine.dispose)
                factory = create_session_factory(engine)
            with factory() as session:
                ready = session.scalar(
                    text(
                        "SELECT to_regclass('rpa_identity_accounts') IS NOT NULL "
                        "AND to_regclass('rpa_portal_rate_limits') IS NOT NULL"
                    )
                )
                if not ready or not schema_is_current(session):
                    raise ConfigurationError("Portal schema is not ready; apply migrations first")
            licenses = DeviceLicenseService(factory, signing_private_key=config.signing_key)
            application.state.portal = PortalServices(
                IdentityService(
                    factory, secret_key=config.secret_key, public_url=config.public_url
                ),
                RateLimitService(factory, secret_key=config.secret_key),
                config,
                TrialService(factory),
                licenses,
                SecretService(factory, secret_key=config.secret_key),
                DownloadService(
                    Path(config.release_dir)
                    if config.release_dir
                    else None
                    if config.app_env == "test"
                    else _WEB.parents[1] / ".runtime" / "releases",
                    public_url=config.public_url,
                    public_key=licenses.public_key,
                    app_env=config.app_env,
                ),
            )
            application.state.customer_runtime = build_customer_runtime(
                factory,
                resources,
                start_workers=start_workers,
                external_workers=external_workers,
                secret_key=config.secret_key,
            )
            yield

    application = FastAPI(
        title=PRODUCT_NAME, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    application.add_middleware(
        LocalRequestBoundary, allowed_hosts={config.hostname}, allow_public_navigation=True
    )
    register_error_handlers(application)

    @application.exception_handler(AuthError)
    async def identity_error(_request: Request, error: AuthError) -> JSONResponse:
        message, status = _AUTH_MESSAGES.get(error.code, ("İstek bilgilerini kontrol edin.", 400))
        return error_response(error.code, message, status)

    application.include_router(router)
    # Import after identity dependencies are defined to keep router modules small.
    from app.web.dependencies import get_services
    from app.web.routes import customer as customer_routes
    from app.web.routes import entitlements, flows, schedules, secrets

    application.dependency_overrides[get_services] = customer_routes.customer_services
    application.include_router(customer_routes.router)
    application.include_router(entitlements.router)
    application.include_router(secrets.router)
    application.include_router(flows.router, dependencies=[Depends(request_throttle)])
    application.include_router(schedules.router, dependencies=[Depends(request_throttle)])

    @application.exception_handler(EntitlementError)
    async def entitlement_error(_request: Request, error: EntitlementError) -> JSONResponse:
        return entitlements.error_response_for(error)

    def page(name: str) -> HTMLResponse:
        content = (_WEB / "templates" / name).read_text(encoding="utf-8")
        content = content.replace("__PRODUCT_NAME__", escape(PRODUCT_NAME))
        content = content.replace(
            "__APP_URL__", escape(config.public_url.rstrip("/") + "/account", quote=True)
        )
        return HTMLResponse(content, headers={"Cache-Control": "no-store"})

    @application.get("/", response_class=HTMLResponse)
    def marketing() -> HTMLResponse:
        return page("marketing.html")

    @application.get("/account", response_class=HTMLResponse)
    @application.get("/verify-email", response_class=HTMLResponse)
    @application.get("/reset-password", response_class=HTMLResponse)
    def customer() -> HTMLResponse:
        return page("portal.html")

    @application.get("/admin", response_class=HTMLResponse)
    def admin() -> HTMLResponse:
        return page("admin.html")

    @application.get("/workspace", response_model=None)
    def workspace(request: Request, portal: Portal) -> HTMLResponse | RedirectResponse:
        try:
            portal.identity.require_approved(session_token(request, portal, "customer"))
        except AuthError:
            return RedirectResponse("/account", status_code=303)
        content = (_WEB / "templates" / "index.html").read_text(encoding="utf-8")
        content = content.replace("__PRODUCT_NAME__", escape(PRODUCT_NAME))
        content = content.replace('<html lang="tr"', '<html lang="tr" data-workspace="customer"')
        content = content.replace(
            '<script src="/static/app.js" defer>',
            '<script src="/static/workspace-bootstrap.js" defer></script>\n'
            '  <script src="/static/app.js" defer>',
        )
        content = content.replace("Geliştirme ortamı", "Müşteri çalışma alanı")
        content = content.replace("GELİŞTİRME", "HESABIM")
        return HTMLResponse(content, headers={"Cache-Control": "no-store"})

    @application.get("/api/downloads", dependencies=[Depends(request_throttle)])
    def downloads(request: Request, portal: Portal) -> dict[str, object]:
        portal.identity.require_approved(session_token(request, portal, "customer"))
        return portal.downloads.list()

    @application.get("/download/{platform}", dependencies=[Depends(request_throttle)])
    def download(
        platform: Literal["linux", "windows"], request: Request, portal: Portal
    ) -> FileResponse:
        portal.identity.require_approved(session_token(request, portal, "customer"))
        artifact = portal.downloads.artifact(platform)
        return FileResponse(
            artifact,
            filename=artifact.name,
            media_type="application/octet-stream",
            headers={"Cache-Control": "private, no-store"},
        )

    application.mount("/static", StaticFiles(directory=_WEB / "static"), name="static")
    return application
