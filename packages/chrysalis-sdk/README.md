# chrysalis-sdk

Client SDKs for the Chrysalis Cloud API and self-hosted Chrysalis deployments.

## What this package contains

- **Python SDK.** Async client with typed responses for every Memoir kernel operation.
- **TypeScript SDK.** Browser and Node-compatible client with full type coverage.

## Install

```bash
# Coming with first alpha release.
pip install chrysalis-sdk          # Python
npm install @chrysalis/sdk         # TypeScript
```

## Quick example (Python)

```python
from chrysalis_sdk import ChrysalisClient

client = ChrysalisClient(api_key="...", base_url="https://api.chrysalis.dev")

result = await client.beliefs.validate(
    agent_id="agent_abc",
    content="The user prefers brevity over depth.",
    source_context="conv_xyz turn 14",
)

print(result.committed, result.belief_quality_score)
```

## License

Apache License 2.0.

---

Make it a great day.
