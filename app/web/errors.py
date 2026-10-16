"""Translate domain failures to the stable, sanitized HTTP error contract."""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException

from app.core.exceptions import (
    DatabaseOperationError,
    IdempotencyConflictError,
    InvalidTransitionError,
    NotFoundError,
    OwnershipLostError,
    RPAError,
    ValidationError,
)
from app.web.responses import error_response

_RESPONSES = {
    NotFoundError: ("NOT_FOUND", "Kayıt bulunamadı.", 404),
    InvalidTransitionError: ("INVALID_TRANSITION", "Bu işlem mevcut durumda uygulanamaz.", 409),
    OwnershipLostError: (
        "OWNERSHIP_LOST",
        "Kayıt sahipliği değişti; güncel durumu yenileyin.",
        409,
    ),
    IdempotencyConflictError: (
        "IDEMPOTENCY_CONFLICT",
        "İşlem anahtarı farklı bir kayıtla eşleşiyor.",
        409,
    ),
    ValidationError: ("VALIDATION_ERROR", "İstek verisi geçersiz veya fazla büyük.", 422),
    DatabaseOperationError: (
        "DATABASE_UNAVAILABLE",
        "Veritabanı işlemi tamamlanamadı; durumu yenileyin.",
        503,
    ),
}


def register_error_handlers(application: FastAPI) -> None:
    @application.exception_handler(RequestValidationError)
    async def request_validation_error(_request, _error_value):
        return error_response(
            "VALIDATION_ERROR", "İstek alanlarını ve veri türlerini kontrol edin.", 422
        )

    @application.exception_handler(RPAError)
    async def application_error(_request, error):
        return error_response(
            *_RESPONSES.get(type(error), ("INTERNAL_ERROR", "İşlem tamamlanamadı.", 500))
        )

    @application.exception_handler(HTTPException)
    async def http_error(_request, error):
        return error_response("HTTP_ERROR", "İstek bu adreste desteklenmiyor.", error.status_code)
