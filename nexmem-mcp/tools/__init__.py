"""Tool definitions for the NexMem MCP server."""

from tools.recall import recall_tool
from tools.remember import remember_tool
from tools.search import search_tool
from tools.set_profile import set_profile_tool

TOOLS = [
    remember_tool,
    recall_tool,
    set_profile_tool,
    search_tool,
]

__all__ = [
    "TOOLS",
    "recall_tool",
    "remember_tool",
    "search_tool",
    "set_profile_tool",
]
