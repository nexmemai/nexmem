recall_tool = {
    "name": "nexmem_recall",
    "description": (
        "Use this when the user asks about prior preferences, facts, decisions, "
        "project context, or information likely stored in long-term memory."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The question or topic to recall relevant memory for.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of memories to retrieve.",
                "minimum": 1,
                "maximum": 20,
                "default": 5,
            },
            "app_id": {
                "type": "string",
                "description": "Optional application scope identifier.",
            },
        },
        "required": ["query"],
    },
}
