# Memory Receipt examples

Each example is a valid v0.1 Memory Receipt or a deliberate counter-example used to test the reference verifier.

## Files

| File | What it demonstrates |
|---|---|
| `valid_dev_receipt.json` | Minimal valid receipt with a local-only anchor. Verifier returns ok=True with a warning that the receipt is dev-only. |
| `expired_receipt.json` | Same shape as valid, but `expires_at` is in the past. Verifier raises VerificationError. |
| `tampered_receipt.json` | Valid signature over the original payload, but a field has been changed after signing. Verifier raises VerificationError on the signature check. |

The examples use a fixed Ed25519 keypair listed in `keys.json` for reproducibility. Do not use these keys for anything other than verifier tests.
