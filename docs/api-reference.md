# Local HTTP API Reference (`v1`)

The FastAPI service (`locale_gate.api.app:create_app`) binds exclusively to loopback (`127.0.0.1` or `::1`).

## Endpoints

### `GET /api/v1/health`
Returns service version, default locale/timezone, parser version, dataset validity, and local `llama.cpp` server reachability.

### `GET /api/v1/model/status`
Returns redacted GGUF artifact metadata (`file_name`, `size_bytes`, `architecture`, `quantization_label`), `llama-server` version, and live `/health` status without exposing host filesystem paths.

### `POST /api/v1/requests`
Evaluates a natural-language user request through LocaleGate.
- **Request Body:**
  ```json
  {
    "text": "Evidentiraj trošak od 1.234,56 € od 03.11.2026. i predaj ga na odobrenje.",
    "locale": "hr-HR",
    "timezone": "Europe/Zagreb",
    "reference_datetime": "2026-11-03T12:00:00+01:00",
    "require_confirmation": true,
    "dry_run": false
  }
  ```
- **Response (`200 OK`):** Discriminated union `GateResult` (`AWAITING_CONFIRMATION`, `NEEDS_CLARIFICATION`, `BLOCKED`, `NO_ACTION`, `EXECUTED`, or `MODEL_ERROR`) with `field_diagnostics`, `canonical_arguments`, and `action_digest`.

### `GET /api/v1/requests` & `GET /api/v1/requests/{identifier}`
Lists recent evaluated requests or retrieves a specific request by `request_id` or `proposal_id`.

### `POST /api/v1/requests/{identifier}/confirm`
Confirms a pending `AWAITING_CONFIRMATION` proposal once. Optionally verifies `{"action_digest": "sha256:..."}`. Commits the simulated record (`submit_expense` with `business_approval_status: "PENDING_MANAGER_APPROVAL"` or `schedule_event`) and returns `ExecutedGateResult`. Subsequent identical confirmations return `idempotent_replay: true`.

### `POST /api/v1/requests/{identifier}/reject`
Marks a pending proposal as `REJECTED` (`CONFIRMATION_REJECTED`) so it can never be executed.

### `GET /api/v1/simulated-state` & `POST /api/v1/simulated-state/reset`
Returns all committed simulated expenses, calendar events, and pending confirmations. Resetting requires `{"confirm": true}`.

### `GET /api/v1/dataset/cases` & `GET /api/v1/evaluations`
Lists curated benchmark cases from `data/benchmark-v1/cases.jsonl` and recorded evaluation runs from `artifacts/evaluations/`.
