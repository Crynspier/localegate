# Security Policy

## Scope and Disclaimer

LocaleGate is a **research and reference implementation** demonstrating deterministic, evidence-backed validation for locale-sensitive LLM tool calls. All side effects (`submit_expense`, `schedule_event`) mutate only an isolated local SQLite or in-memory simulation database. LocaleGate is **not** certified or intended for direct connection to production payment, banking, accounting, or calendar infrastructure.

## Security Model Summary

- **Untrusted model output:** Every token, tool selection, and field proposal returned by the LLM is treated as untrusted input.
- **Immutable trusted context:** Locale (`hr-HR`, `en-US`, `en-GB`), IANA timezone (`Europe/Zagreb`, etc.), reference datetime, tool allowlist, and confirmation requirements are supplied by the trusted application context and cannot be overwritten by user text or model proposals.
- **Exact source quotation resolution:** The model cannot supply character offsets or normalized values. It must supply verbatim substrings from the original request, which the deterministic resolver verifies for unique occurrence before parsing.
- **Single-use digest-bound user confirmation:** State-changing simulated actions require explicit confirmation bound to a SHA-256 canonical action digest, request ID, and expiration timestamp.
- **Loopback-only network posture:** Both the local FastAPI service (`127.0.0.1:8765`) and the local `llama-server` (`127.0.0.1:8080`) bind exclusively to loopback by default. Outbound connections to non-loopback hosts are rejected by the model adapter unless explicitly overridden for testing.
- **Artifact path confinement:** Evaluation artifact downloads validate resolved paths against the configured `artifacts/evaluations` root to prevent path traversal.

## Reporting a Vulnerability

If you discover a bypass in the deterministic evidence resolver, locale parsers, confirmation digest verification, or local API confinement, please document the minimal reproducing test case (input string, trusted context, and untrusted proposal payload) and report it through the repository issue tracker or maintainer contact.
