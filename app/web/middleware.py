"""ASGI request boundaries for the development management application."""

from urllib.parse import urlsplit

from app.web.responses import error_response

_MAX_BODY = 128 * 1024
_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class LocalRequestBoundary:
    """Reject cross-site writes, DNS rebinding and oversized streaming bodies."""

    def __init__(self, app, allowed_hosts, allow_public_navigation=False):
        self.app = app
        self.allowed_hosts = allowed_hosts
        self.allow_public_navigation = allow_public_navigation

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {}
        host_count = 0
        for key, value in scope.get("headers", []):
            name = key.decode("latin1").lower()
            if name == "host":
                host_count += 1
            headers[name] = value.decode("latin1")
        started = False

        async def secured_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                extra = dict(_HEADERS)
                if scope["path"].startswith("/api/"):
                    extra["Cache-Control"] = "no-store"
                message = dict(message)
                message["headers"] = list(message.get("headers", [])) + [
                    (key.lower().encode("ascii"), value.encode("ascii"))
                    for key, value in extra.items()
                ]
            await send(message)

        async def reject(code, message, status):
            await error_response(code, message, status)(scope, receive, secured_send)

        host = headers.get("host", "")
        try:
            host_url = urlsplit("http://" + host)
            valid_host = (
                host_count == 1
                and host_url.hostname in self.allowed_hosts
                and host_url.username is None
                and host_url.password is None
                and not host_url.path
                and not host_url.query
                and not host_url.fragment
                and host_url.port != 0
            )
        except ValueError:
            valid_host = False
        if not valid_host:
            await reject("REQUEST_REJECTED", "İstek adresine izin verilmiyor.", 403)
            return
        origin = headers.get("origin")
        if origin is not None:
            try:
                parsed = urlsplit(origin)
                valid_origin = (
                    parsed.scheme == scope.get("scheme", "http")
                    and parsed.netloc.lower() == host.lower()
                    and not parsed.path
                    and not parsed.query
                    and not parsed.fragment
                    and parsed.username is None
                    and parsed.password is None
                )
            except ValueError:
                valid_origin = False
            if not valid_origin:
                await reject("REQUEST_REJECTED", "İstek kaynağına izin verilmiyor.", 403)
                return
        public_navigation = (
            self.allow_public_navigation
            and scope["method"] in {"GET", "HEAD"}
            and not scope["path"].startswith("/api/")
        )
        if headers.get("sec-fetch-site") == "cross-site" and not public_navigation:
            await reject("REQUEST_REJECTED", "Siteler arası isteğe izin verilmiyor.", 403)
            return
        if scope["method"] in {"POST", "PUT", "PATCH", "DELETE"}:
            if headers.get("x-rpa-request") != "1":
                await reject("REQUEST_REJECTED", "Uygulama istek başlığı gerekli.", 403)
                return
            media_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                await reject("UNSUPPORTED_MEDIA_TYPE", "JSON içerik gerekli.", 415)
                return
        try:
            content_length = int(headers.get("content-length", "0"))
        except ValueError:
            await reject("VALIDATION_ERROR", "İstek boyutu geçersiz.", 400)
            return
        if content_length < 0:
            await reject("VALIDATION_ERROR", "İstek boyutu geçersiz.", 400)
            return
        if content_length > _MAX_BODY:
            await reject("PAYLOAD_TOO_LARGE", "İstek en fazla 128 KiB olabilir.", 413)
            return
        body = bytearray()
        while True:
            part = await receive()
            if part["type"] == "http.disconnect":
                return
            chunk = part.get("body", b"")
            if len(body) + len(chunk) > _MAX_BODY:
                await reject("PAYLOAD_TOO_LARGE", "İstek en fazla 128 KiB olabilir.", 413)
                return
            body.extend(chunk)
            if not part.get("more_body", False):
                break
        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        try:
            await self.app(scope, bounded_receive, secured_send)
        except Exception:
            # Suppress raw driver details in ASGI error logs as well as responses.
            if not started:
                await reject("INTERNAL_ERROR", "İşlem tamamlanamadı.", 500)
