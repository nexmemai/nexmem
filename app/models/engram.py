"""Engram ORM model — compressed, embedded memory units — Day 2/4."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Column, DateTime, Float, Integer, JSON, String,
)
from sqlalchemy.dialects.postgresql import UUID
from pgvector.sqlalchemy import Vector

from app.database import Base


class Engram(Base):
    """
    A distilled, semantically-compressed memory unit.
    Produced by EngramProcessor after ingesting raw text.
    """

    __tablename__ = "engrams"

    # --- Identity ---
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    # Short fingerprint used for graph edge references (e.g. "a1b2c3d4e5f6")
    engram_id = Column(String(12), nullable=False, index=True)

    # --- Compressed text & embedding ---
    distilled_text = Column(String, nullable=False)
    # 384-dim vector from all-MiniLM-L6-v2 (filled in Day 4)
    dense_embedding = Column(Vector(384), nullable=True)

    # --- NLP extractions ---
    actions = Column(JSON, default=list)          # verbs (may include NOT_verb)
    objects = Column(JSON, default=list)          # noun objects
    entities = Column(JSON, default=list)         # named entities
    negated_actions = Column(JSON, default=list)  # verbs under negation

    # --- Scoring ---
    salience_scores = Column(JSON, default=dict)  # token → score map
    connections = Column(JSON, default=list)      # list of related engram_ids

    # --- Compression stats ---
    original_length = Column(Integer, nullable=True)
    compressed_length = Column(Integer, nullable=True)
    compression_ratio = Column(Float, nullable=True)

    # --- Provenance ---
    source_type = Column(String, nullable=True)   # e.g. "episodic", "api", "rag"

    # --- Timestamps ---
    created_at = Column(
        DateTime(timezone=True), default=datetime.utcnow,
        nullable=False, index=True
    )
    last_accessed_at = Column(DateTime(timezone=True), nullable=True)
