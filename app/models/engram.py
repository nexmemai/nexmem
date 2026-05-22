"""Engram ORM model — Day 4."""

import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import Column, String, Text, Float, Integer, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.database import Base


class Engram(Base):
    """Compressed memory unit produced by EngramProcessor."""

    __tablename__ = "engrams"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    # app_id is nullable; NULL means "user-scoped, no app". Multi-app
    # tenants use this column to isolate engram contexts. Added in
    # migration 012_add_app_id_to_engrams (R-H5).
    app_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    engram_id: Mapped[str] = mapped_column(
        String(12), nullable=False, index=True
    )
    distilled_text: Mapped[str] = mapped_column(Text, nullable=False)
    dense_embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(384), nullable=True
    )
    actions: Mapped[List[str]] = mapped_column(JSON, default=list)
    objects: Mapped[List[str]] = mapped_column(JSON, default=list)
    entities: Mapped[List[str]] = mapped_column(JSON, default=list)
    negated_actions: Mapped[List[str]] = mapped_column(JSON, default=list)
    salience_scores: Mapped[dict] = mapped_column(JSON, default=dict)
    connections: Mapped[List[str]] = mapped_column(JSON, default=list)
    original_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    compressed_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    compression_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    source_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True
    )
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )