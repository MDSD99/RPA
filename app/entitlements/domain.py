"""Pure entitlement policy and stable, non-sensitive failure codes."""

from calendar import monthrange
from datetime import UTC, datetime


class EntitlementError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


TrialError = EntitlementError


def calendar_month_after(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("Trial timestamps must include a timezone")
    value = value.astimezone(UTC)
    year, month = (value.year + 1, 1) if value.month == 12 else (value.year, value.month + 1)
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


def timestamp(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None
