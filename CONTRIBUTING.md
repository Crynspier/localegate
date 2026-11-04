# Contributing to LocaleGate

Thank you for your interest in contributing to LocaleGate.

## Scope and Principles

LocaleGate is a research and reference implementation of an evidence-backed, deterministic locale-aware execution boundary for structured LLM tool calls, focusing on Croatian/EU (`hr-HR`) and English (`en-US`, `en-GB`) conventions.

When contributing, keep these core constraints intact:

1. **Deterministic core independence:** Everything under `contracts`, `evidence`, `locales`, `parsers`, `canonical`, `tools`, `policy`, and `gate` must run without loading a neural model, Torch, Transformers, or network services.
2. **Exact-quote provenance:** The model proposes verbatim substrings (`quote` and optional `context_excerpt`); backend code resolves unique code-point offsets in the unmodified source string.
3. **No silent guessing:** Ambiguous separators, missing years, missing currencies, and ambiguous/nonexistent DST wall times must fail closed (`AMBIGUOUS` or `INVALID`), never guess silently.
4. **No float money:** All financial parsing and validation uses `decimal.Decimal` and fixed-point strings.
5. **Simulated side effects only:** Never add real payment, accounting, email, or calendar connectors.
6. **Local privacy:** Do not commit `.env`, `.gguf` files, raw user logs, or personal filesystem paths.

## Development Workflow

```powershell
uv sync --dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
uv run pytest
```

For frontend changes in `web/`:

```powershell
npm --prefix web install
npm --prefix web run lint
npm --prefix web run typecheck
npm --prefix web run test
npm --prefix web run build
```

For dataset changes in `data/benchmark-v1/`:

```powershell
uv run locale-gate eval validate-dataset
```
