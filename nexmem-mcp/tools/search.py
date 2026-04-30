search_tool = {
    "name": "nexmem_search",
    "description": (
        "Use this to search memory for a specific entity, topic, or phrase when "
        "you need raw matching memory snippets rather than a composed context."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Search query for memory snippets.",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of matching snippets to return.",
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
