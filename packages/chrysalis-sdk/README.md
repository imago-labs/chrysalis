# chrysalis-sdk

Developer SDK for the Chrysalis platform. Async Python client for validating beliefs, querying the audit log, and streaming governance events from any application.

Works against Chrysalis Cloud at api.chrysalis.dev and any self-hosted Chrysalis deployment that speaks the same HTTP API.

## Install

```bash
pip install chrysalis-sdk
```

Python 3.11+.

## Quick start

```python
from chrysalis_sdk import ChrysalisClient

async with ChrysalisClient(
    base_url="https://api.chrysalis.dev",
    api_key="sk-...",
) as client:
    # Validate a belief through the MEMOIR pipeline.
    result = await client.beliefs.validate(
        session_id="conv_xyz",
        key="user_preference",
        content="The user prefers brevity over depth.",
        source_reference="conv_xyz turn 14",
    )
    print(result.approved, result.epistemic_tag, result.critique_verdict)

    # Query the audit log.
    records = await client.audit.list(session_id="conv_xyz", limit=20)

    # Stream governance events in real time.
    async for event in client.events.stream(session_id="conv_xyz"):
        print(event.type, event.key, event.epistemic_tag)
```

## Resources

| Namespace | Methods |
|---|---|
| `client.beliefs` | `validate`, `verify`, `list` |
| `client.audit` | `list`, `session_summary`, `trust_score` |
| `client.agent` | `chat` |
| `client.events` | `stream` (SSE) |
| `client.health` | health check (no auth) |

## Configuration

| Argument | Notes |
|---|---|
| `base_url` | API root URL. Required. |
| `api_key` | Sent as `X-API-Key` header on every request. Required unless the deployment runs with `CHRYSALIS_AUTH_REQUIRED=0`. |
| `timeout` | Request timeout in seconds. Default 30. |
| `client` | Optional preconfigured `httpx.AsyncClient` to share a connection pool. |

## License

Apache License 2.0.

---

Make it a great day.
