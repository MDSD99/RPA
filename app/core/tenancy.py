"""Explicit execution ownership; legacy data remains in a reserved development tenant."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import true
from sqlalchemy.sql.elements import ColumnElement

from app.core.exceptions import NotFoundError
from app.core.validation import validate_uuid

if TYPE_CHECKING:
    from app.database.models import TenantOwned

LEGACY_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


class TenantScope:
    """Repository scope. System access is explicitly granted by trusted composition."""

    def __init__(self, tenant_id: UUID = LEGACY_TENANT_ID, *, system: bool = False) -> None:
        self.tenant_id = validate_uuid(tenant_id)
        if type(system) is not bool:
            raise TypeError("System scope must be explicit")
        self.system = system

    def predicate(self, model: type[TenantOwned]) -> ColumnElement[bool]:
        return true() if self.system else model.tenant_id == self.tenant_id

    def require_owner(self, *records: TenantOwned) -> None:
        if not self.system and any(record.tenant_id != self.tenant_id for record in records):
            raise NotFoundError("Resource not found")
