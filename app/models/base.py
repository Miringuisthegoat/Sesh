import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enum_type(enum_cls):
    """Portable enum: stored as VARCHAR holding the member *value* (no native PG enum, simpler migrations)."""
    return sa.Enum(enum_cls, native_enum=False, length=24, validate_strings=True,
                   values_callable=lambda e: [m.value for m in e])


class PublicIdMixin:
    """Non-guessable identifier exposed in URLs/APIs. Internal integer ids are never exposed."""
    public_id = mapped_column(sa.Uuid, default=uuid.uuid4, unique=True, nullable=False)


class TimestampMixin:
    created_at = mapped_column(sa.DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = mapped_column(sa.DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
