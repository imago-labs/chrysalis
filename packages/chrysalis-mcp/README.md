# chrysalis-mcp

Model Context Protocol server for Chrysalis. Lets Claude Code, Claude Desktop, and any other MCP client validate beliefs, query the audit log, and request critique through Chrysalis.

## What it exposes

- `validate_belief`. Run a belief through the Memoir validation pipeline.
- `query_audit_log`. Read the agent's audit log with filters.
- `request_critique`. Get a Critic evaluation of a belief or recent action.
- `attest`. Anchor a payload to the configured attestation chain.

## Install

```bash
# Coming with first alpha release.
pip install chrysalis-mcp
```

## Configure with Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "chrysalis": {
      "command": "uvx",
      "args": ["chrysalis-mcp"],
      "env": {
        "CHRYSALIS_API_KEY": "..."
      }
    }
  }
}
```

## License

Apache License 2.0.

---

Make it a great day.
