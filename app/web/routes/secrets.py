"""Write credentials and list metadata; plaintext credentials are never returned."""

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import Field

from app.web.portal import Input, Portal, check_csrf, request_throttle, session_token

router = APIRouter(dependencies=[Depends(request_throttle)])


class StoreCredential(Input):
    kind: Literal["smtp", "http", "browser"]
    values: dict[str, str]
    expected_version: int | None = Field(default=None, ge=1)


class DeleteCredential(Input):
    expected_version: int = Field(ge=1)


@router.get("/api/secrets")
def list_credentials(request: Request, portal: Portal) -> dict[str, object]:
    user = portal.identity.require_approved(session_token(request, portal, "customer"))
    return portal.secrets.list(UUID(user.tenant_id))


@router.put("/api/secrets/{reference}")
def store_credential(
    reference: str, body: StoreCredential, request: Request, portal: Portal
) -> dict[str, object]:
    user = portal.identity.require_approved(check_csrf(request, portal, "customer"))
    return {
        "credential": portal.secrets.put(
            UUID(user.tenant_id),
            UUID(user.id),
            reference,
            body.kind,
            body.values,
            body.expected_version,
        )
    }


@router.delete("/api/secrets/{reference}")
def delete_credential(
    reference: str, body: DeleteCredential, request: Request, portal: Portal
) -> dict[str, object]:
    user = portal.identity.require_approved(check_csrf(request, portal, "customer"))
    portal.secrets.delete(UUID(user.tenant_id), UUID(user.id), reference, body.expected_version)
    return {"message": "Bağlantı bilgisi silindi."}
