"""SQLAlchemy ORM models for the AI Memory Layer."""

import uuid
from datetime import datetime
from typing import Optional, List

from sqlalchemy import (
    Column, String, Text, Boolean, Float,
    DateTime, ForeignKey, Index, CheckConstraint,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY, TSVECTOR
from sqlalchemy.orm import relationship, Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.database import Base

# ── shared column helpers ──────────────────────────────────────────────────────

def _user_id_col():
    """Standard user_id FK column — UUID, indexed, required."""
    return Column(UUID(as_uuid=True), nullable=False, index=True)

def _app_id_col():
    """Optional app_id for multi-app memory scoping."""
    return Column(UUID(as_uuid=True), nullable=True, index=True)


class EpisodicMemory(Base):
    """Episodic memory: time-stamped conversation history."""

    __tablename__ = "episodic_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id = _user_id_col()
    app_id  = _app_id_col()
    session_id: Mapped[str] = mapped_column(Text, nullable=False)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # FTS vector for full-text search
    text_search: Mapped[Optional[TSVECTOR]] = mapped_column(TSVECTOR, nullable=True)
    # NOTE: column name stays 'metadata' in DB; attribute renamed to avoid
    # conflict with SQLAlchemy's reserved DeclarativeBase.metadata attribute
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    tags: Mapped[Optional[List[str]]] = mapped_column(ARRAY(Text), default=list)
    store_episodic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    consolidated: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    consolidated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    importance_score: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    semantic_memories = relationship(
        "SemanticMemory", back_populates="episodic", cascade="all, delete"
    )

    __table_args__ = (
        Index("idx_episodic_user_session", "user_id", "session_id"),
    )


class SemanticMemory(Base):
    """Semantic memory: vector embeddings for meaning-based retrieval."""

    __tablename__ = "semantic_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id = _user_id_col()
    app_id  = _app_id_col()
    episodic_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("episodic_memory.id", ondelete="SET NULL"),
        nullable=True,
    )
    vector = mapped_column(Vector(1536), nullable=False)
    embedding_model: Mapped[str] = mapped_column(
        Text, nullable=False, default="text-embedding-3-small"
    )
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content_preview: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # FTS vector for full-text search
    text_search: Mapped[Optional[TSVECTOR]] = mapped_column(TSVECTOR, nullable=True)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    index_semantic: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    episodic = relationship("EpisodicMemory", back_populates="semantic_memories")


class ProceduralMemory(Base):
    """Procedural memory: user preferences, settings, and workflows."""

    __tablename__ = "procedural_memory"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    app_id = _app_id_col()  # Optional app_id for multi-app scoping
    settings: Mapped[dict] = mapped_column(JSONB, default=dict)
    workflows: Mapped[List[dict]] = mapped_column(JSONB, default=list)
    store_procedural: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow,
        onupdate=datetime.utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    __table_args__ = (
        UniqueConstraint("user_id", "app_id", name="uq_procedural_user_app"),
    )


class KnowledgeNode(Base):
    """Knowledge graph node for associative memory."""

    __tablename__ = "knowledge_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id = _user_id_col()
    app_id  = _app_id_col()
    label: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    properties: Mapped[dict] = mapped_column(JSONB, default=dict)
    store_associative: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    outgoing_edges = relationship(
        "KnowledgeEdge",
        foreign_keys="KnowledgeEdge.from_node_id",
        back_populates="from_node",
        cascade="all, delete",
    )
    incoming_edges = relationship(
        "KnowledgeEdge",
        foreign_keys="KnowledgeEdge.to_node_id",
        back_populates="to_node",
        cascade="all, delete",
    )


class KnowledgeEdge(Base):
    """Knowledge graph edge for associative memory."""

    __tablename__ = "knowledge_edges"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id = _user_id_col()
    app_id = _app_id_col()
    from_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    to_node_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_nodes.id", ondelete="CASCADE"),
        nullable=False,
    )
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0)
    extra_metadata: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )

    # Relationships
    from_node = relationship(
        "KnowledgeNode",
        foreign_keys=[from_node_id],
        back_populates="outgoing_edges",
    )
    to_node = relationship(
        "KnowledgeNode",
        foreign_keys=[to_node_id],
        back_populates="incoming_edges",
    )

    __table_args__ = (
        CheckConstraint("from_node_id != to_node_id", name="no_self_loop"),
    )
