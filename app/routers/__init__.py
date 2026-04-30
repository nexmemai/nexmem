"""Routers package — export all API routers."""

from app.routers import episodic, semantic, procedural, graph, rag, auth, health, memory, gdpr

__all__ = [
    "episodic",
    "semantic",
    "procedural",
    "graph",
    "rag",
    "auth",
    "health",
    "memory",
    "gdpr",
]
