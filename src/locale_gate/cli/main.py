"""Typer + Rich CLI for LocaleGate (§10.1)."""

from __future__ import annotations

import contextlib
import json
import platform
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from locale_gate import __version__
from locale_gate.agent.orchestrator import LocalAgentOrchestrator
from locale_gate.benchmark.dataset_builder import build_and_write_benchmark_v1
from locale_gate.benchmark.runner import BenchmarkRunner, compare_two_runs
from locale_gate.benchmark.schema import validate_benchmark_dataset
from locale_gate.canonical.types import get_tzdb_provenance
from locale_gate.config.settings import (
    KNOWN_LOCAL_MODELS,
    is_loopback_host,
    load_settings,
    resolve_known_model_spec,
)
from locale_gate.contracts.models import TrustedRequestContext
from locale_gate.locales.profiles import ISO_4217_TABLE_VERSION
from locale_gate.models.adapter import (
    OpenAICompatibleLocalAdapter,
    run_model_preflight_smoke_test,
)
from locale_gate.models.gguf_inspector import (
    discover_llama_server_binary,
    inspect_gguf_artifact,
    query_llama_server_version,
)
from locale_gate.parsers.date_time import parse_date, parse_datetime
from locale_gate.parsers.money import parse_money
from locale_gate.tools.simulator import SimulatedStore


def _ensure_utf8_stdio() -> None:
    """Ensure Windows stdout/stderr pipes can emit UTF-8 characters (č, ć, đ, š, ž, €)."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            with contextlib.suppress(Exception):
                reconfigure(encoding="utf-8", errors="replace")


_ensure_utf8_stdio()

app = typer.Typer(
    name="locale-gate",
    help="LocaleGate: Evidence-backed, deterministic locale-aware execution boundary for LLM tool calls.",
    no_args_is_help=True,
)
model_app = typer.Typer(
    help="Inspect, serve, and smoke-test local GGUF models (Qwen3.5-4B & Gemma-4-E2B-It)."
)
parse_app = typer.Typer(help="Deterministically parse money, date, and datetime strings.")
eval_app = typer.Typer(help="Validate benchmark dataset, run evaluations, and compare results.")
db_app = typer.Typer(help="Inspect or reset the local SQLite simulated database.")
api_app = typer.Typer(help="Run the local loopback-only HTTP API server.")

app.add_typer(model_app, name="model")
app.add_typer(parse_app, name="parse")
app.add_typer(eval_app, name="eval")
app.add_typer(db_app, name="db")
app.add_typer(api_app, name="api")

console = Console(legacy_windows=False)

EXIT_INVALID_INPUT = 2
EXIT_MODEL_UNAVAILABLE = 3
EXIT_INTERNAL_ERROR = 4


@app.command("doctor")
def doctor_cmd(
    skip_hash: bool = typer.Option(
        False, "--skip-hash", help="Skip full GGUF SHA-256 calculation."
    ),
) -> None:
    """Report Python/platform, tzdata/ISO-4217 provenance, llama-server version, GGUF manifests, and endpoint health."""
    settings = load_settings()
    llama_bin = discover_llama_server_binary(settings.model.llama_server_bin)
    llama_ver = query_llama_server_version(llama_bin)
    model_path = settings.model.resolve_model_path()

    adapter = OpenAICompatibleLocalAdapter(settings.model)
    health = adapter.check_health()

    artifact_info = None
    if model_path and model_path.is_file():
        manifest = inspect_gguf_artifact(
            model_path,
            path_alias=settings.model.path_alias,
            context_size=settings.model.context_size,
            gpu_layers=settings.model.gpu_layers,
            thinking_enabled=settings.model.enable_thinking,
            compute_hash=not skip_hash,
            llama_cpp_version=llama_ver,
        )
        artifact_info = manifest.to_public_dict()

    report = {
        "locale_gate_version": __version__,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "tzdb_provenance": get_tzdb_provenance(),
        "iso4217_table_version": ISO_4217_TABLE_VERSION,
        "llama_server_available": llama_bin is not None,
        "llama_server_version": llama_ver,
        "model_file_found": bool(model_path and model_path.is_file()),
        "model_manifest": artifact_info,
        "available_local_models": settings.model.discover_local_models(),
        "endpoint_health": health,
        "effective_config": settings.to_public_dict(),
    }
    console.print_json(json.dumps(report, indent=2, ensure_ascii=False))


@model_app.command("inspect")
def model_inspect_cmd(
    model: str = typer.Option(
        "qwen35-q4km",
        "--model",
        "-m",
        help="Model key (`qwen35-q4km`, `gemma4-e2b-q4km`, or `all`).",
    ),
    skip_hash: bool = typer.Option(False, "--skip-hash", help="Skip SHA-256 calculation."),
) -> None:
    """Inspect local GGUF model header, metadata, byte size, and SHA-256."""
    settings = load_settings()
    if model.strip().lower() == "all":
        manifests = []
        for item_spec in KNOWN_LOCAL_MODELS.values():
            mpath = settings.model.resolve_model_path(model_selector=item_spec.key)
            if mpath and mpath.is_file():
                mf = inspect_gguf_artifact(
                    mpath,
                    path_alias=item_spec.path_alias,
                    context_size=settings.model.context_size,
                    gpu_layers=settings.model.gpu_layers,
                    thinking_enabled=settings.model.enable_thinking,
                    compute_hash=not skip_hash,
                )
                manifests.append(mf.to_public_dict())
        console.print_json(json.dumps({"models": manifests}, indent=2, ensure_ascii=False))
        return

    spec = resolve_known_model_spec(model)
    model_path = settings.model.resolve_model_path(model_selector=model)
    if model_path is None or not model_path.is_file():
        console.print(f"[red]Error:[/red] Local GGUF model file for {model!r} not found.")
        raise typer.Exit(code=EXIT_MODEL_UNAVAILABLE)

    alias = spec.path_alias if spec else settings.model.path_alias
    manifest = inspect_gguf_artifact(
        model_path,
        path_alias=alias,
        context_size=settings.model.context_size,
        gpu_layers=settings.model.gpu_layers,
        thinking_enabled=settings.model.enable_thinking,
        compute_hash=not skip_hash,
    )
    console.print_json(json.dumps(manifest.to_public_dict(), indent=2, ensure_ascii=False))


@model_app.command("serve")
def model_serve_cmd(
    model: str = typer.Option(
        "qwen35-q4km",
        "--model",
        "-m",
        help="Model key (`qwen35-q4km` or `gemma4-e2b-q4km`).",
    ),
    host: str = typer.Option("127.0.0.1", "--host", help="Loopback bind host."),
    port: int = typer.Option(8080, "--port", help="Local port for llama-server."),
    ctx_size: int = typer.Option(4096, "--ctx-size", help="Context size (default 4096)."),
    gpu_layers: int = typer.Option(
        0, "--n-gpu-layers", help="GPU layers (default 0 CPU baseline)."
    ),
) -> None:
    """Launch local llama-server bound strictly to loopback (127.0.0.1)."""
    if not is_loopback_host(host):
        console.print(
            f"[red]Security error:[/red] Refusing to bind llama-server to non-loopback host {host!r}."
        )
        raise typer.Exit(code=EXIT_INVALID_INPUT)

    settings = load_settings()
    llama_bin = discover_llama_server_binary(settings.model.llama_server_bin)
    if llama_bin is None or not llama_bin.is_file():
        console.print("[red]Error:[/red] llama-server executable not found.")
        raise typer.Exit(code=EXIT_MODEL_UNAVAILABLE)

    spec = resolve_known_model_spec(model)
    model_path = settings.model.resolve_model_path(model_selector=model)
    if model_path is None or not model_path.is_file():
        console.print(f"[red]Error:[/red] GGUF model file for {model!r} not found.")
        raise typer.Exit(code=EXIT_MODEL_UNAVAILABLE)

    alias = spec.path_alias if spec else settings.model.path_alias
    reasoning_mode = "on" if settings.model.enable_thinking else "off"
    cmd = [
        str(llama_bin),
        "--model",
        str(model_path),
        "--alias",
        alias,
        "--host",
        host,
        "--port",
        str(port),
        "--ctx-size",
        str(ctx_size),
        "--parallel",
        str(settings.model.parallel_slots),
        "--n-gpu-layers",
        str(gpu_layers),
        "--jinja",
        "--reasoning",
        reasoning_mode,
    ]
    console.print(
        f"Starting llama-server on [bold]http://{host}:{port}[/bold] "
        f"(ctx={ctx_size}, gpu_layers={gpu_layers}, alias={alias}, reasoning={reasoning_mode})..."
    )
    proc = subprocess.run(cmd, check=False)
    raise typer.Exit(code=proc.returncode)


@model_app.command("smoke-test")
def model_smoke_test_cmd(
    native_tools: bool = typer.Option(
        False,
        "--native-tools",
        help="Test native OpenAI tool_calls mode instead of structured JSON schema mode.",
    ),
) -> None:
    """Run the 5-case §8.5 preflight smoke test against the running local llama-server."""
    settings = load_settings()
    adapter = OpenAICompatibleLocalAdapter(
        settings.model,
        prefer_native_tools=native_tools,
        prompts_dir=Path(settings.app.prompts_dir),
    )
    health = adapter.check_health()
    if not health.get("reachable"):
        console.print(
            f"[red]Model server unreachable at {settings.model.base_url}[/red]: {health.get('error')}"
        )
        raise typer.Exit(code=EXIT_MODEL_UNAVAILABLE)

    summary = run_model_preflight_smoke_test(adapter)
    console.print_json(json.dumps(summary, indent=2, ensure_ascii=False))
    if not summary["all_passed"]:
        raise typer.Exit(code=1)


@parse_app.command("money")
def parse_money_cmd(
    value: str = typer.Argument(..., help="Raw money string, e.g. '1.234,56 €'."),
    locale: str = typer.Option("hr-HR", "--locale", "-l", help="BCP 47 locale profile."),
    currency: str | None = typer.Option(None, "--currency", "-c", help="Optional currency quote."),
) -> None:
    """Deterministically parse a locale-sensitive money quotation."""
    res = parse_money(value, locale, explicit_currency_quote=currency)
    payload = {
        "status": res.status.value,
        "reason_code": res.reason_code.value if res.reason_code else None,
        "message": res.message,
        "rule_id": res.rule_id,
        "parser_version": res.parser_version,
        "canonical": res.value.to_dict() if res.value else None,
    }
    console.print_json(json.dumps(payload, indent=2, ensure_ascii=False))
    if not res.is_ok:
        raise typer.Exit(code=EXIT_INVALID_INPUT)


@parse_app.command("date")
def parse_date_cmd(
    value: str = typer.Argument(..., help="Raw date string, e.g. '03.11.2026.'."),
    locale: str = typer.Option("hr-HR", "--locale", "-l", help="BCP 47 locale profile."),
    reference_datetime: str = typer.Option(
        "2026-11-03T12:00:00+01:00",
        "--reference-datetime",
        help="Timezone-aware reference datetime for relative dates.",
    ),
) -> None:
    """Deterministically parse a locale-sensitive date quotation."""
    ref_dt = datetime.fromisoformat(reference_datetime)
    res = parse_date(value, locale, reference_datetime=ref_dt)
    payload = {
        "status": res.status.value,
        "reason_code": res.reason_code.value if res.reason_code else None,
        "message": res.message,
        "rule_id": res.rule_id,
        "parser_version": res.parser_version,
        "canonical": res.value.to_dict() if res.value else None,
    }
    console.print_json(json.dumps(payload, indent=2, ensure_ascii=False))
    if not res.is_ok:
        raise typer.Exit(code=EXIT_INVALID_INPUT)


@parse_app.command("datetime")
def parse_datetime_cmd(
    value: str = typer.Argument(..., help="Raw datetime string, e.g. '03.11.2026. u 09:30'."),
    locale: str = typer.Option("hr-HR", "--locale", "-l", help="BCP 47 locale profile."),
    timezone: str = typer.Option("Europe/Zagreb", "--timezone", "-z", help="IANA timezone."),
    reference_datetime: str = typer.Option(
        "2026-11-03T12:00:00+01:00",
        "--reference-datetime",
        help="Timezone-aware reference datetime.",
    ),
) -> None:
    """Deterministically parse a locale-sensitive datetime with DST fold/gap verification."""
    ref_dt = datetime.fromisoformat(reference_datetime)
    res = parse_datetime(
        value,
        locale,
        trusted_timezone=timezone,
        reference_datetime=ref_dt,
    )
    payload = {
        "status": res.status.value,
        "reason_code": res.reason_code.value if res.reason_code else None,
        "message": res.message,
        "rule_id": res.rule_id,
        "parser_version": res.parser_version,
        "candidates": list(res.candidates),
        "canonical": res.value.to_dict() if res.value else None,
    }
    console.print_json(json.dumps(payload, indent=2, ensure_ascii=False))
    if not res.is_ok:
        raise typer.Exit(code=EXIT_INVALID_INPUT)


@app.command("run")
def run_cmd(
    text: str = typer.Option(..., "--text", "-t", help="Natural-language user request."),
    locale: str = typer.Option("hr-HR", "--locale", "-l", help="Trusted BCP 47 locale."),
    timezone: str = typer.Option(
        "Europe/Zagreb", "--timezone", "-z", help="Trusted IANA timezone."
    ),
    reference_datetime: str = typer.Option(
        "2026-11-03T12:00:00+01:00",
        "--reference-datetime",
        help="Fixed timezone-aware reference datetime.",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--persist",
        help="Default dry-run mode does not write to SQLite. Pass --persist to queue confirmation.",
    ),
) -> None:
    """Run a user request through the local model adapter and deterministic execution gate."""
    settings = load_settings()
    store = SimulatedStore(
        db_url=settings.app.db_url,
        store_raw_user_text=settings.logging.store_raw_user_text,
        confirmation_ttl_seconds=settings.app.confirmation_ttl_seconds,
    )
    adapter = OpenAICompatibleLocalAdapter(
        settings.model,
        prompts_dir=Path(settings.app.prompts_dir),
    )
    health = adapter.check_health()
    if not health.get("reachable"):
        console.print(
            f"[red]Model server unreachable at {settings.model.base_url}[/red]. "
            "Start it with `locale-gate model serve`."
        )
        raise typer.Exit(code=EXIT_MODEL_UNAVAILABLE)

    orchestrator = LocalAgentOrchestrator(adapter=adapter, store=store)
    ctx = TrustedRequestContext(
        request_id=f"req_{uuid.uuid4().hex[:12]}",
        text=text,
        locale=locale,
        timezone=timezone,
        reference_datetime=datetime.fromisoformat(reference_datetime),
        require_confirmation=settings.app.require_confirmation_for_writes,
        dry_run=dry_run,
    )
    outcome = orchestrator.run_turn(ctx)
    out_dict = json.loads(outcome.gate_result.model_dump_json())
    out_dict["telemetry"] = outcome.telemetry.__dict__
    out_dict["dry_run"] = dry_run
    console.print_json(json.dumps(out_dict, indent=2, ensure_ascii=False))


@app.command("confirm")
def confirm_cmd(
    proposal_id: str = typer.Argument(..., help="Pending proposal_id or request_id to confirm."),
    digest: str = typer.Option(..., "--digest", "-d", help="Required expected action_digest."),
    token: str = typer.Option(..., "--token", "-t", help="Required single-use confirmation_token."),
) -> None:
    """Confirm a pending proposal once and execute the simulated state change."""
    settings = load_settings()
    store = SimulatedStore(
        db_url=settings.app.db_url,
        store_raw_user_text=settings.logging.store_raw_user_text,
        confirmation_ttl_seconds=settings.app.confirmation_ttl_seconds,
    )
    try:
        res = store.confirm_and_execute(
            proposal_id,
            expected_action_digest=digest,
            confirmation_token=token,
            require_token=True,
        )
        console.print_json(res.model_dump_json(indent=2))
    except KeyError as exc:
        console.print(f"[red]Not found:[/red] {exc}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc
    except ValueError as exc:
        console.print(f"[red]Confirmation rejected:[/red] {exc}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc


@app.command("reject")
def reject_cmd(
    proposal_id: str = typer.Argument(..., help="Pending proposal_id or request_id to reject."),
) -> None:
    """Reject a pending proposal so it can never be executed."""
    settings = load_settings()
    store = SimulatedStore(
        db_url=settings.app.db_url,
        store_raw_user_text=settings.logging.store_raw_user_text,
        confirmation_ttl_seconds=settings.app.confirmation_ttl_seconds,
    )
    try:
        res = store.reject_pending(proposal_id)
        console.print_json(res.model_dump_json(indent=2))
    except (KeyError, ValueError) as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=EXIT_INVALID_INPUT) from exc


@eval_app.command("validate-dataset")
def eval_validate_dataset_cmd(
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Rebuild data/benchmark-v1 before validating."
    ),
    adversarial: bool = typer.Option(
        False, "--adversarial", help="Validate data/adversarial-v1/cases.jsonl."
    ),
    adversarial_v2: bool = typer.Option(
        False, "--adversarial-v2", help="Validate data/adversarial-v2/cases.jsonl."
    ),
) -> None:
    """Validate benchmark dataset schema, split integrity, and deterministic oracle consistency."""
    settings = load_settings()
    if adversarial:
        adv_file = Path("./data/adversarial-v1/cases.jsonl")
        summary = validate_benchmark_dataset(adv_file)
    elif adversarial_v2:
        adv_file = Path("./data/adversarial-v2/cases.jsonl")
        summary = validate_benchmark_dataset(adv_file)
    else:
        data_dir = Path(settings.app.data_dir)
        cases_file = data_dir / "cases.jsonl"
        if rebuild or not cases_file.is_file():
            summary = build_and_write_benchmark_v1(data_dir)
        else:
            summary = validate_benchmark_dataset(cases_file)

    console.print_json(json.dumps(summary, indent=2, ensure_ascii=False))
    if not summary["valid"]:
        raise typer.Exit(code=EXIT_INVALID_INPUT)


@eval_app.command("run")
def eval_run_cmd(
    model: str = typer.Option(
        "qwen35-q4km",
        "--model",
        help="Model identifier (`qwen35-q4km`, `gemma4-e2b-q4km`, or `oracle-only`).",
    ),
    suite: str = typer.Option(
        "pilot",
        "--suite",
        help="Suite: `smoke` (12), `pilot` (60), `test` (480), `dev` (120), `full` (600), `adversarial` (60), or `adversarial-v2` (30).",
    ),
    profile: str | None = typer.Option(
        None,
        "--profile",
        help="Sampling profile name (defaults to the selected model's primary profile).",
    ),
    run_id: str | None = typer.Option(
        None, "--run-id", help="Optional run ID to create or resume."
    ),
    skip_hash: bool = typer.Option(
        False, "--skip-hash", help="Skip full GGUF SHA-256 calculation."
    ),
) -> None:
    """Run baseline & ablation vs LocaleGate evaluation suite and write self-contained artifacts."""
    valid_suites = {"smoke", "pilot", "test", "dev", "full", "adversarial", "adversarial-v2"}
    if suite not in valid_suites:
        console.print(f"[red]Invalid suite {suite!r}. Choose from {sorted(valid_suites)}.[/red]")
        raise typer.Exit(code=EXIT_INVALID_INPUT)

    settings = load_settings()
    if suite == "adversarial":
        cases_file = Path("./data/adversarial-v1/cases.jsonl")
    elif suite == "adversarial-v2":
        cases_file = Path("./data/adversarial-v2/cases.jsonl")
    else:
        cases_file = Path(settings.app.data_dir) / "cases.jsonl"
        if not cases_file.is_file():
            build_and_write_benchmark_v1(Path(settings.app.data_dir))

    spec = resolve_known_model_spec(model) if model != "oracle-only" else None
    adapter = None
    if model != "oracle-only":
        if spec is not None:
            settings.model.path_alias = spec.path_alias
        live_adapter = OpenAICompatibleLocalAdapter(
            settings.model,
            prompts_dir=Path(settings.app.prompts_dir),
        )
        health = live_adapter.check_health()
        if not health.get("reachable"):
            console.print(
                f"[red]Model server unreachable at {settings.model.base_url}[/red]. "
                "Start `llama-server` or pass `--model oracle-only`."
            )
            raise typer.Exit(code=EXIT_MODEL_UNAVAILABLE)
        adapter = live_adapter

    effective_profile = profile or (spec.default_profile if spec else settings.model.active_profile)
    runner = BenchmarkRunner(settings=settings, adapter=adapter, model_spec=spec)
    result = runner.run_evaluation(
        cases_path=cases_file,
        suite=suite,
        run_id=run_id,
        profile_name=effective_profile,
        compute_gguf_hash=not skip_hash,
    )
    console.print_json(json.dumps(result, indent=2, ensure_ascii=False))


@eval_app.command("compare")
def eval_compare_cmd(
    run_id_a: str = typer.Argument(..., help="First evaluation run ID."),
    run_id_b: str = typer.Argument(..., help="Second evaluation run ID."),
) -> None:
    """Compare two evaluation runs from artifacts/evaluations/."""
    settings = load_settings()
    base = Path(settings.app.artifacts_dir) / "evaluations"
    dir_a = base / run_id_a
    dir_b = base / run_id_b
    if not dir_a.is_dir() or not dir_b.is_dir():
        console.print("[red]One or both evaluation run directories do not exist.[/red]")
        raise typer.Exit(code=EXIT_INVALID_INPUT)
    comp = compare_two_runs(dir_a, dir_b)
    console.print_json(json.dumps(comp, indent=2, ensure_ascii=False))


@eval_app.command("report")
def eval_report_cmd(
    run_id: str = typer.Argument(..., help="Evaluation run ID to display."),
) -> None:
    """Print the generated Markdown report for an evaluation run."""
    settings = load_settings()
    report_file = Path(settings.app.artifacts_dir) / "evaluations" / run_id / "report.md"
    if not report_file.is_file():
        console.print(f"[red]Report not found at {report_file}[/red]")
        raise typer.Exit(code=EXIT_INVALID_INPUT)
    console.print(report_file.read_text(encoding="utf-8"))


@db_app.command("status")
def db_status_cmd() -> None:
    """Inspect simulated database schema version, counts, audit chain integrity, and pending confirmations."""
    settings = load_settings()
    store = SimulatedStore(
        db_url=settings.app.db_url,
        store_raw_user_text=settings.logging.store_raw_user_text,
        confirmation_ttl_seconds=settings.app.confirmation_ttl_seconds,
    )
    state = store.get_simulated_state()
    table = Table(title="LocaleGate Simulated Database Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Schema Version", str(state["schema_version"]))
    table.add_row("Audit Chain Intact", str(state.get("audit_chain_intact", True)))
    table.add_row("Committed Expenses", str(state["counts"]["expenses"]))
    table.add_row("Committed Events", str(state["counts"]["events"]))
    table.add_row("Pending Confirmations", str(state["counts"]["pending_confirmations"]))
    table.add_row("Audit Events", str(state["counts"]["audit_events"]))
    console.print(table)


@db_app.command("reset")
def db_reset_cmd(
    yes: bool = typer.Option(False, "--yes", help="Confirm resetting all simulated tables."),
) -> None:
    """Reset all simulated database tables (requires explicit --yes confirmation)."""
    if not yes:
        console.print("[red]Refusing to reset database without --yes flag.[/red]")
        raise typer.Exit(code=EXIT_INVALID_INPUT)
    settings = load_settings()
    store = SimulatedStore(db_url=settings.app.db_url)
    store.reset_all(confirmed=True)
    console.print("[green]Simulated database reset completed.[/green]")


@api_app.command("serve")
def api_serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host", help="Loopback bind host."),
    port: int = typer.Option(8765, "--port", help="Port for local FastAPI server."),
) -> None:
    """Start the local loopback-only FastAPI server."""
    if not is_loopback_host(host):
        console.print(
            f"[red]Security error:[/red] Refusing to bind API to non-loopback host {host!r}."
        )
        raise typer.Exit(code=EXIT_INVALID_INPUT)
    uvicorn.run("locale_gate.api.app:create_app", host=host, port=port, factory=True)


if __name__ == "__main__":
    app()
