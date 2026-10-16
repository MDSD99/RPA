"""Safe application errors; messages never contain connection or record payloads."""


class RPAError(Exception):
    """Base error for execution infrastructure."""


class ValidationError(RPAError, ValueError):
    """An argument violates the public service contract."""


class NotFoundError(RPAError, LookupError):
    """The requested entity does not exist."""


class InvalidTransitionError(RPAError):
    """The requested state change is not currently allowed."""


class OwnershipLostError(RPAError):
    """A worker's lease expired or its ownership token was invalidated."""


class IdempotencyConflictError(RPAError):
    """An idempotency key or business identity conflicts with an existing record."""


class DatabaseOperationError(RPAError):
    """A sanitized database failure; a commit acknowledgement may be uncertain."""
