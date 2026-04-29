"""Pydantic schemas for request/response validation."""

from datetime import datetime
from typing import Optional, Any
from uuid import UUID
from pydantic import BaseModel, Field


# ==========================================
# Episodic Memory Schemas
# ==========================================

class EpisodicCreate(BaseModel):
    """Schema for creating a new episodic memory."""
    session_id: str
    timestamp: Optional[datetime] = None
    content: str
    metadata: dict = {}
    tags: list[str] = []
    store_episodic: bool = True


class EpisodicResponse(BaseModel):
    """Schema for episodic memory response."""
    id: UUID
    user_id: str
    session_id: str
    timestamp: datetime
    content: str
    metadata: dict
    tags: list[str]
    store_episodic: bool
    created_at: datetime

    class Config:
        from_attributes = True


# ==========================================
# Semantic Memory Schemas
# ==========================================

class SemanticCreate(BaseModel):
    """Schema for creating a semantic memory entry."""
    episodic_id: Optional[UUID] = None
    content: str
    metadata: dict = {}
    summary: Optional[str] = None
    embedding_model: str = "text-embedding-3-small"
    index_semantic: bool = True


class SemanticSearchRequest(BaseModel):
    """Schema for semantic search request."""
    query: str
    k: int = Field(default=5, ge=1, le=50)


class SemanticMatch(BaseModel):
    """Schema for a semantic search result."""
    id: UUID
    summary: Optional[str] = None
    content_preview: Optional[str] = None
    similarity: float
    episodic_context: Optional[str] = None
    metadata: dict = {}


# ==========================================
# Procedural Memory Schemas
# ==========================================

class ProceduralUpsert(BaseModel):
    """Schema for upserting procedural memory."""
    user_id: str
    settings: dict = {}
    workflows: list[dict] = []


class ProceduralResponse(BaseModel):
    """Schema for procedural memory response."""
    id: UUID
    user_id: str
    settings: dict
    workflows: list[dict]
    store_procedural: bool
    updated_at: datetime
    created_at: datetime

    class Config:
        from_attributes = True


# ==========================================
# Knowledge Graph Schemas
# ==========================================

class NodeCreate(BaseModel):
    """Schema for creating a knowledge graph node."""
    label: str
    type: str
    properties: dict = {}


class NodeResponse(BaseModel):
    """Schema for knowledge graph node response."""
    id: UUID
    user_id: str
    label: str
    type: str
    properties: dict
    store_associative: bool
    created_at: datetime

    class Config:
        from_attributes = True


class EdgeCreate(BaseModel):
    """Schema for creating a knowledge graph edge."""
    from_node_id: UUID
    to_node_id: UUID
    relation: str
    weight: float = 1.0
    metadata: dict = {}


class EdgeResponse(BaseModel):
    """Schema for knowledge graph edge response."""
    id: UUID
    user_id: str
    from_node_id: UUID
    to_node_id: UUID
    relation: str
    weight: float
    metadata: dict
    created_at: datetime

    class Config:
        from_attributes = True


class PathQuery(BaseModel):
    """Schema for graph path query."""
    from_node_id: UUID
    to_node_id: UUID
    max_hops: int = Field(default=3, ge=1, le=10)


class PathResponse(BaseModel):
    """Schema for graph path response."""
    found: bool
    path: list[str]
    hops: int
    nodes: list[NodeResponse] = []


# ==========================================
# RAG / LLM Schemas
# ==========================================

class RAGRequest(BaseModel):
    """Schema for RAG-enhanced chat request."""
    user_id: str
    message: str
    session_id: Optional[str] = None
    include_episodic: bool = True
    include_semantic: bool = True
    include_procedural: bool = True
    include_graph: bool = False
    top_k: int = Field(default=5, ge=1, le=20)
    app_id: Optional[str] = Field(default=None, description="App ID for scoping")


class RAGResponse(BaseModel):
    """Schema for RAG response."""
    reply: str
    retrieved_episodes: list[str] = []
    retrieved_semantics: list[str] = []
    retrieved_procedural: Optional[dict] = None
    retrieved_graph_nodes: list[str] = []
    retrieved_graph_edges: list[str] = []
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    latency_ms: Optional[float] = None


# ==========================================
# Memory Stats Schema
# ==========================================

class MemoryStats(BaseModel):
    """Schema for memory statistics."""
    user_id: str
    episodic_count: int
    semantic_count: int
    procedural_count: int
    graph_node_count: int
    graph_edge_count: int
    total_memories: int


class RecentMemory(BaseModel):
    """Schema for recent memory item."""
    memory_type: str  # episodic, semantic, procedural
    id: str
    content: str
    created_at: Optional[str] = None
    emoji_label: str = ""
