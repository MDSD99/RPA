"""Shared response envelopes for request boundaries and exception translation."""

from fastapi.responses import JSONResponse


def error_response(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)
