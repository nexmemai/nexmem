# nexmem-mcp

MCP server for NexMem long-term memory tools.

## Run

```bash
uvx nexmem-mcp --api-key nxm_xxxxx --base-url https://nexmem-api.onrender.com
```

The package also exposes a compatibility command:

```bash
uvx mnemo-mcp --api-key nxm_xxxxx
```

## Cursor / Claude Desktop

```json
{
  "mcpServers": {
    "mnemo": {
      "command": "uvx",
      "args": ["nexmem-mcp", "--api-key", "nxm_xxxxx"]
    }
  }
}
```

## Tools

- `nexmem_remember`: store important durable facts, preferences, and decisions.
- `nexmem_recall`: retrieve composed context for a user query.
- `nexmem_set_profile`: store stable profile preferences.
- `nexmem_search`: return raw matching memory snippets.

## Development

```bash
pip install -e .
python -m build
```
