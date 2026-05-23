"""
Models package — import everything here so Alembic autogenerate sees all tables.
Import order matters: Base must be defined before any model that uses it.
"""

# Core database base
from app.database import Base  # noqa: F401

# Models (import all so SQLAlchemy metadata is populated)
from app.models.user import User, APIKey          # noqa: F401
from app.models.apps import App                   # noqa: F401  (Phase 4 / P4-B1)
from app.models.app_usage import AppUsage         # noqa: F401  (Phase 4 / P4-B5)
from app.models.auth import RefreshToken          # noqa: F401
from app.models.memory import (                   # noqa: F401
    EpisodicMemory,
    SemanticMemory,
    ProceduralMemory,
    KnowledgeNode,
    KnowledgeEdge,
)
from app.models.engram import Engram              # noqa: F401

__all__ = [
    "Base",
    "User",
    "APIKey",
    "App",
    "AppUsage",
    "RefreshToken",
    "EpisodicMemory",
    "SemanticMemory",
    "ProceduralMemory",
    "KnowledgeNode",
    "KnowledgeEdge",
    "Engram",
]
