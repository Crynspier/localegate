# Threat Model & Security Controls

## Assets Protected

1. **Simulated Financial & Scheduling Integrity:** Preventing silent corruption of monetary amounts (`1.234,56 €` vs `1.23`), calendar dates (`03.11.2026.` vs `2026-03-11`), and DST boundary times (`Europe/Zagreb` spring gap and autumn fold).
2. **Host System & Local Privacy:** Ensuring the service binds strictly to loopback (`127.0.0.1` / `::1`), makes zero outbound non-loopback network requests, never executes shell commands or arbitrary filesystem operations, and never logs raw user text unless explicitly opted in.

## Threat Matrix & Mitigations

| Threat ID | Vector | Mitigation | Reason Code / Enforcement |
| :--- | :--- | :--- | :--- |
| `TM-01` | **Hallucinated or Altered Value:** Model invents an amount or date not present in user text, or normalizes `"1.234,56 €"` into `"1234.56"` inside the quote. | Stage 3 requires exact verbatim substring match in untouched original user text. | `EVIDENCE_QUOTE_NOT_FOUND` (`BLOCKED`) |
| `TM-02` | **Repeated Span / Wrong Role Binding:** User text contains multiple numbers (e.g. invoice number vs amount, subtotal vs total, retracted figures). | Stage 3 enforces unique occurrence or unique `context_excerpt`. Stage 3b verifies semantic role markers and flags ungrounded role bindings. | `EVIDENCE_AMBIGUOUS_MULTIPLE_MATCHES`, `ROLE_BINDING_CONFLICT`, `ROLE_BINDING_AMBIGUOUS` |
| `TM-03` | **Prompt Injection & Fake Tool JSON:** User text embeds instructions to execute unapproved tools or bypass validation. | Stage 1a blocks forbidden bypass keys (`PROPOSAL_CANONICAL_BYPASS_ATTEMPT`). Stage 2 enforces a closed tool allowlist. Stage 6 requires human confirmation even when all fields are valid. | `PROPOSAL_CANONICAL_BYPASS_ATTEMPT`, `TOOL_UNKNOWN`, `AWAITING_CONFIRMATION` |
| `TM-04` | **Unicode Homoglyphs & Bidirectionality Spoofing:** Input or quote uses lookalike punctuation, non-ASCII digits, or bidi overrides. | Deterministic parsers explicitly reject prohibited lookalike separators and non-ASCII digits. Display strings strip control and bidi characters. | `MONEY_INVALID_FORMAT`, `DATE_INVALID_FORMAT` |
| `TM-05` | **Confirmation Replay or Tampering:** Replaying a confirmation token or confirming a modified payload. | Confirmation is bound to a domain-separated `action_digest` (`"locale-gate:action-digest:v1"`). Mismatched digests are rejected; exact replays return `idempotent_replay=True`. | `CONFIRMATION_DIGEST_MISMATCH`, `CONFIRMATION_REJECTED`, `CONFIRMATION_INVALID_OR_EXPIRED` |
| `TM-06` | **Localhost SSRF / Path Traversal:** Attempting to configure a remote model URL or traverse paths in evaluation endpoints. | `is_loopback_host` / `is_loopback_url` reject non-loopback endpoints; `_validate_safe_segment` rejects `..`, `/`, and `\`. | `ValueError` / `HTTP 400` |
| `TM-07` | **Duplicate JSON Keys:** Adversary uses duplicate keys in JSON (e.g. `{"quote": "10", "quote": "9999"}`) to exploit parser divergence between schema validator and execution logic. | `parse_untrusted_proposal_json` uses `object_pairs_hook` to reject duplicate keys immediately. | `DuplicateJSONKeyError` (`BLOCKED`) |
| `TM-08` | **Unbounded JSON Payloads:** Oversized JSON strings causing denial-of-service or memory exhaustion. | Strict byte limits (`max_bytes=65536`) and string length bounds enforced before parsing. | `UntrustedJSONBoundsError` (`BLOCKED`) |
| `TM-09` | **Unsupported Currency Submissions:** Submitting transactions in retired, unsupported, or fictitious currencies (e.g. `DEM`, `XYZ`). | Strict ISO 4217 active table enforcement; unrecognized currency markers follow generic rejection. | `CURRENCY_UNSUPPORTED` (`BLOCKED`) |
| `TM-10` | **Context Tampering:** Mutation of trusted application context during evaluation or gate execution. | `TrustedRequestContext.integrity_snapshot()` generates a cryptographic SHA-256 seal verified at every pipeline stage. | `ContextTamperingError` (`BLOCKED`) |
| `TM-11` | **Audit Log Tampering:** Modifying, deleting, or reordering historical execution logs. | Every state change appends to an immutable SQLite audit log where each event includes `previous_event_hash`, forming a SHA-256 hash chain. | Audit log hash mismatch on verification |
| `TM-12` | **Confirmation Token Forgery:** Guessing or crafting an unearned confirmation token. | Single-use 160-bit cryptographically secure random bearer capability tokens (`lgc_...`), with only their SHA-256 hash persisted and constant-time verification. | `CONFIRMATION_INVALID_OR_EXPIRED` (`BLOCKED`) |
