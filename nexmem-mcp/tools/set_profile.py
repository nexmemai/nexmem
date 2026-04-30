set_profile_tool = {
    "name": "nexmem_set_profile",
    "description": (
        "Use this to store durable user profile data such as preferences, "
        "working style, communication style, or stable configuration."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "key": {
                "type": "string",
                "description": "Profile key to set, for example 'preferred_language'.",
            },
            "value": {
                "description": "JSON-serializable value to store for the profile key.",
            },
            "app_id": {
                "type": "string",
                "description": "Optional application scope identifier.",
            },
        },
        "required": ["key", "value"],
    },
}
