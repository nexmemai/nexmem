remember_tool = {
    "name": "nexmem_remember",
    "description": (
        "Use this after the user shares important preferences, facts, or "
        "decisions that should persist across sessions. Do NOT use for "
        "small talk or temporary calculations."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "The information to store in long-term memory.",
            },
            "app_id": {
                "type": "string",
                "description": "Optional application scope identifier.",
            },
            "metadata": {
                "type": "object",
                "description": "Optional metadata to attach to the memory.",
                "additionalProperties": True,
            },
        },
        "required": ["text"],
    },
}
