# Chrysalis Examples

Runnable demos using only the open kernel and stub implementations.

## quickstart/

Minimal end-to-end Memoir pipeline. No API keys, no external services. Verifies your install is working.

```bash
cd quickstart
pip install chrysalis-interfaces
python run.py
```

## More examples coming with first alpha

- `with-bedrock/`. Drop in a real Critic backed by AWS Bedrock Claude.
- `with-ollama/`. BYOM (Bring Your Own Model). Drop in a local Ollama Critic for fully offline operation.
- `with-platform/`. Connect to Chrysalis Cloud and use real Oracle, Mirror, Resonance, and Shield implementations.

---

Make it a great day.
