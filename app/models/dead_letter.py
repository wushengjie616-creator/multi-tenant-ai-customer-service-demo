"""Persisted terminal failures for tenant-scoped diagnosis and replay."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DeadLetter(Base):
    __tablename__ = "dead_letters"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    trace_id: Mapped[str] = mapped_column(String(128), index=True)
    original_event_id: Mapped[str | None] = mapped_column(String(128))
    original_subject: Mapped[str] = mapped_column(String(128))
    error_type: Mapped[str] = mapped_column(String(128))
    error: Mapped[str] = mapped_column(Text)
    metadata_json: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    status: Mapped[str] = mapped_column(String(24), server_default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    replayed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
