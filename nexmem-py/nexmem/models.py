"""Data models returned by the NexMem SDK."""

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Episode:
    """Result returned after storing a memory."""

    episodic_id: str | None = None
    semantic_id: str | None = None
    engram_id: str | None = None
    nodes_created: int = 0
    edges_created: int = 0
    message: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Episode":
        return cls(
            episodic_id=data.get("episodic_id"),
            semantic_id=data.get("semantic_id"),
            engram_id=data.get("engram_id"),
            nodes_created=data.get("nodes_created", 0),
            edges_created=data.get("edges_created", 0),
            message=data.get("message", ""),
            raw=data,
        )


@dataclass(slots=True)
class Memories:
    """Convenience wrapper for assembled recall content."""

    content: str
    semantic_hits: list[dict[str, Any]] = field(default_factory=list)
    recent_episodes: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class Context:
    """Context assembled from all memory sources."""

    content: str
    memories: Memories
    engram_context: str = ""
    preferences: dict[str, Any] = field(default_factory=dict)
    graph_context: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Context":
        content = data.get("assembled_context", "")
        return cls(
            content=content,
            memories=Memories(
                content=content,
                semantic_hits=data.get("semantic_hits", []),
                recent_episodes=data.get("recent_episodes", []),
            ),
            engram_context=data.get("engram_context", ""),
            preferences=data.get("preferences", {}),
            graph_context=data.get("graph_context", {}),
            metadata=data.get("metadata", {}),
            raw=data,
        )
