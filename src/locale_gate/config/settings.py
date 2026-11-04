"""Typed configuration and safe privacy defaults for LocaleGate."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def is_loopback_host(host: str) -> bool:
    """Return True if the given host string resolves to a local loopback interface."""
    cleaned = host.strip().lower()
    if cleaned in {"localhost", "127.0.0.1", "::1", "[::1]"}:
        return True
    try:
        ip = ipaddress.ip_address(cleaned.strip("[]"))
        return ip.is_loopback
    except ValueError:
        return False


def is_loopback_url(url: str) -> bool:
    """Return True if the HTTP(S) URL points to a loopback address."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.hostname:
        return False
    return is_loopback_host(parsed.hostname)


@dataclass(frozen=True)
class LocalGGUFSpec:
    """Metadata specification for a supported local GGUF model."""

    key: str
    filename: str
    path_alias: str
    model_family: str
    architecture: str
    quantization: str
    context_length: int
    expected_sha256: str
    expected_size_bytes: int
    default_profile: str


KNOWN_LOCAL_MODELS: dict[str, LocalGGUFSpec] = {
    "qwen35-q4km": LocalGGUFSpec(
        key="qwen35-q4km",
        filename="Qwen3.5-4B-Q4_K_M.gguf",
        path_alias="local-qwen35-4b-q4km",
        model_family="Qwen3.5-4B",
        architecture="qwen35",
        quantization="Q4_K_M",
        context_length=262144,
        expected_sha256="00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
        expected_size_bytes=2740937888,
        default_profile="qwen35_q4km_primary",
    ),
    "gemma4-e2b-q4km": LocalGGUFSpec(
        key="gemma4-e2b-q4km",
        filename="gemma-4-E2B-it-Q4_K_M.gguf",
        path_alias="local-gemma4-e2b-it-q4km",
        model_family="Gemma-4-E2B-It",
        architecture="gemma4",
        quantization="Q4_K_M",
        context_length=131072,
        expected_sha256="740185b21d22ceb83a11c3aa62ad5842ef32c70f6096d756bbee85a1e4ec34b8",
        expected_size_bytes=3106738272,
        default_profile="gemma4_e2b_q4km_primary",
    ),
}


def resolve_known_model_spec(selector: str | None) -> LocalGGUFSpec | None:
    """Resolve a model key, alias, or filename to a LocalGGUFSpec."""
    if not selector:
        return KNOWN_LOCAL_MODELS["qwen35-q4km"]
    cleaned = selector.strip().lower()
    for spec in KNOWN_LOCAL_MODELS.values():
        if cleaned in {
            spec.key.lower(),
            spec.filename.lower(),
            spec.path_alias.lower(),
            spec.model_family.lower(),
        }:
            return spec
        if cleaned in spec.key.lower() or cleaned in spec.filename.lower():
            return spec
    return None


class AppSettings(BaseModel):
    """Application-level runtime settings."""

    model_config = ConfigDict(extra="forbid")

    bind_host: str = Field(default="127.0.0.1")
    port: int = Field(default=8765, ge=1024, le=65535)
    default_locale: str = Field(default="hr-HR")
    default_timezone: str = Field(default="Europe/Zagreb")
    require_confirmation_for_writes: bool = Field(default=True)
    dry_run_by_default: bool = Field(default=True)
    allow_non_loopback_bind: bool = Field(default=False)
    confirmation_ttl_seconds: int = Field(default=900, ge=30, le=86400)
    max_request_body_bytes: int = Field(default=65536, ge=1024, le=1048576)
    db_url: str = Field(default="sqlite:///./artifacts/locale_gate.db")
    artifacts_dir: str = Field(default="./artifacts")
    data_dir: str = Field(default="./data/benchmark-v1")
    prompts_dir: str = Field(default="./prompts")

    @field_validator("bind_host")
    @classmethod
    def validate_bind_host(cls, v: str) -> str:
        return v.strip()


class ModelProfileConfig(BaseModel):
    """Sampling and thinking profile for local model runs."""

    model_config = ConfigDict(extra="forbid")

    name: str
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    top_k: int = Field(default=1, ge=0, le=1000)
    min_p: float = Field(default=0.0, ge=0.0, le=1.0)
    presence_penalty: float = Field(default=0.0, ge=-2.0, le=2.0)
    max_tokens: int = Field(default=512, ge=32, le=4096)
    enable_thinking: bool = Field(default=False)
    seed: int = Field(default=20261103)


DEFAULT_MODEL_PROFILES: dict[str, ModelProfileConfig] = {
    "qwen35_q4km_primary": ModelProfileConfig(
        name="qwen35_q4km_primary",
        temperature=0.0,
        top_p=1.0,
        top_k=1,
        min_p=0.0,
        presence_penalty=0.0,
        max_tokens=512,
        enable_thinking=False,
        seed=20261103,
    ),
    "qwen35_q4km_recommended_nonthinking": ModelProfileConfig(
        name="qwen35_q4km_recommended_nonthinking",
        temperature=0.7,
        top_p=0.8,
        top_k=20,
        min_p=0.0,
        presence_penalty=1.5,
        max_tokens=512,
        enable_thinking=False,
        seed=20261103,
    ),
    "qwen35_q4km_thinking_ablation": ModelProfileConfig(
        name="qwen35_q4km_thinking_ablation",
        temperature=0.0,
        top_p=1.0,
        top_k=1,
        min_p=0.0,
        presence_penalty=0.0,
        max_tokens=1024,
        enable_thinking=True,
        seed=20261103,
    ),
    "gemma4_e2b_q4km_primary": ModelProfileConfig(
        name="gemma4_e2b_q4km_primary",
        temperature=0.0,
        top_p=1.0,
        top_k=1,
        min_p=0.0,
        presence_penalty=0.0,
        max_tokens=512,
        enable_thinking=False,
        seed=20261103,
    ),
    "gemma4_e2b_q4km_recommended": ModelProfileConfig(
        name="gemma4_e2b_q4km_recommended",
        temperature=1.0,
        top_p=0.95,
        top_k=64,
        min_p=0.0,
        presence_penalty=0.0,
        max_tokens=512,
        enable_thinking=False,
        seed=20261103,
    ),
    "gemma4_e2b_q4km_thinking_ablation": ModelProfileConfig(
        name="gemma4_e2b_q4km_thinking_ablation",
        temperature=0.0,
        top_p=1.0,
        top_k=1,
        min_p=0.0,
        presence_penalty=0.0,
        max_tokens=1024,
        enable_thinking=True,
        seed=20261103,
    ),
}


class ModelSettings(BaseModel):
    """Local llama.cpp GGUF model runtime configuration."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(default="llama_cpp")
    base_url: str = Field(default="http://127.0.0.1:8080/v1")
    allow_non_loopback_url: bool = Field(default=False)
    model_path_env: str = Field(default="LOCALE_GATE_MODEL_PATH")
    model_path: str | None = Field(default=None)
    path_alias: str = Field(default="local-qwen35-4b-q4km")
    llama_server_bin: str | None = Field(default=None)
    context_size: int = Field(default=4096, ge=512, le=32768)
    parallel_slots: int = Field(default=1, ge=1, le=8)
    gpu_layers: int = Field(default=0, ge=0, le=128)
    enable_thinking: bool = Field(default=False)
    request_timeout_seconds: float = Field(default=180.0, ge=1.0, le=600.0)
    connect_timeout_seconds: float = Field(default=10.0, ge=0.5, le=60.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    max_output_tokens: int = Field(default=512, ge=32, le=4096)
    active_profile: str = Field(default="qwen35_q4km_primary")
    profiles: dict[str, ModelProfileConfig] = Field(
        default_factory=lambda: dict(DEFAULT_MODEL_PROFILES)
    )

    def resolve_model_path(
        self,
        cwd: Path | None = None,
        model_selector: str | None = None,
    ) -> Path | None:
        """Locate a local GGUF file via selector, explicit config, env var, or workspace root."""
        base = cwd or Path.cwd()
        if model_selector:
            spec = resolve_known_model_spec(model_selector)
            if spec is not None:
                for p in (base / spec.filename, base / "models" / spec.filename):
                    if p.is_file():
                        return p.resolve()
            cand = Path(model_selector)
            if not cand.is_absolute():
                cand = (base / cand).resolve()
            if cand.is_file():
                return cand

        env_val = os.environ.get(self.model_path_env) or os.environ.get("LOCALE_GATE_MODEL_PATH")
        raw = self.model_path or env_val
        if raw:
            spec = resolve_known_model_spec(raw)
            if spec is not None:
                for p in (base / spec.filename, base / "models" / spec.filename):
                    if p.is_file():
                        return p.resolve()
            candidate = Path(raw)
            if not candidate.is_absolute():
                candidate = (base / candidate).resolve()
            return candidate
        # Default fallback in project working directory
        for spec in KNOWN_LOCAL_MODELS.values():
            for p in (base / spec.filename, base / "models" / spec.filename):
                if p.is_file():
                    return p.resolve()
        return None

    def discover_local_models(self, cwd: Path | None = None) -> list[dict[str, Any]]:
        """Discover all configured/present local GGUF models without leaking personal paths."""
        base = cwd or Path.cwd()
        out: list[dict[str, Any]] = []
        for spec in KNOWN_LOCAL_MODELS.values():
            found_path: Path | None = None
            for p in (base / spec.filename, base / "models" / spec.filename):
                if p.is_file():
                    found_path = p.resolve()
                    break
            out.append(
                {
                    "key": spec.key,
                    "filename": spec.filename,
                    "path_alias": spec.path_alias,
                    "model_family": spec.model_family,
                    "architecture": spec.architecture,
                    "quantization": spec.quantization,
                    "context_length": spec.context_length,
                    "default_profile": spec.default_profile,
                    "present": found_path is not None,
                    "file_size_bytes": found_path.stat().st_size if found_path else None,
                    "expected_sha256": spec.expected_sha256,
                }
            )
        return out


class LoggingSettings(BaseModel):
    """Privacy-first logging and export settings."""

    model_config = ConfigDict(extra="forbid")

    store_raw_user_text: bool = Field(default=False)
    store_raw_model_output: bool = Field(default=False)
    export_absolute_model_path: bool = Field(default=False)


class Settings(BaseSettings):
    """Root typed configuration for LocaleGate."""

    model_config = SettingsConfigDict(
        env_prefix="LOCALE_GATE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app: AppSettings = Field(default_factory=AppSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    def validate_security_posture(self) -> None:
        """Enforce loopback-only binding and outbound model URL safety defaults."""
        if not self.app.allow_non_loopback_bind and not is_loopback_host(self.app.bind_host):
            raise ValueError(
                f"Non-loopback API bind_host '{self.app.bind_host}' is prohibited by default. "
                "Set allow_non_loopback_bind=True only with explicit authentication."
            )
        if not self.model.allow_non_loopback_url and not is_loopback_url(self.model.base_url):
            raise ValueError(
                f"Non-loopback model base_url '{self.model.base_url}' is prohibited by default."
            )

    def to_public_dict(self, cwd: Path | None = None) -> dict[str, Any]:
        """Return effective configuration without leaking personal filesystem paths."""
        resolved = self.model.resolve_model_path(cwd=cwd)
        model_exists = bool(resolved and resolved.is_file())
        return {
            "app": {
                "bind_host": self.app.bind_host,
                "port": self.app.port,
                "default_locale": self.app.default_locale,
                "default_timezone": self.app.default_timezone,
                "require_confirmation_for_writes": self.app.require_confirmation_for_writes,
                "dry_run_by_default": self.app.dry_run_by_default,
                "confirmation_ttl_seconds": self.app.confirmation_ttl_seconds,
            },
            "model": {
                "provider": self.model.provider,
                "base_url": self.model.base_url,
                "path_alias": self.model.path_alias,
                "model_file_configured": model_exists,
                "available_local_models": self.model.discover_local_models(cwd=cwd),
                "context_size": self.model.context_size,
                "parallel_slots": self.model.parallel_slots,
                "gpu_layers": self.model.gpu_layers,
                "enable_thinking": self.model.enable_thinking,
                "active_profile": self.model.active_profile,
                "max_output_tokens": self.model.max_output_tokens,
            },
            "logging": {
                "store_raw_user_text": self.logging.store_raw_user_text,
                "store_raw_model_output": self.logging.store_raw_model_output,
                "export_absolute_model_path": self.logging.export_absolute_model_path,
            },
        }


def load_settings(yaml_path: Path | None = None) -> Settings:
    """Load settings from optional YAML file and environment variables."""
    data: dict[str, Any] = {}
    if yaml_path and yaml_path.is_file():
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data.update(loaded)

    settings = Settings(**data)

    # Apply convenience flat environment variables if present
    env_map: list[tuple[str, Any]] = [
        ("LOCALE_GATE_BIND_HOST", ("app", "bind_host", str)),
        ("LOCALE_GATE_PORT", ("app", "port", int)),
        ("LOCALE_GATE_DEFAULT_LOCALE", ("app", "default_locale", str)),
        ("LOCALE_GATE_DEFAULT_TIMEZONE", ("app", "default_timezone", str)),
        (
            "LOCALE_GATE_REQUIRE_CONFIRMATION",
            ("app", "require_confirmation_for_writes", _parse_bool),
        ),
        ("LOCALE_GATE_DRY_RUN_DEFAULT", ("app", "dry_run_by_default", _parse_bool)),
        ("LOCALE_GATE_DB_URL", ("app", "db_url", str)),
        ("LOCALE_GATE_LLAMA_BASE_URL", ("model", "base_url", str)),
        ("LOCALE_GATE_MODEL_PATH", ("model", "model_path", str)),
        ("LOCALE_GATE_LLAMA_SERVER_BIN", ("model", "llama_server_bin", str)),
        ("LOCALE_GATE_CONTEXT_SIZE", ("model", "context_size", int)),
        ("LOCALE_GATE_PARALLEL_SLOTS", ("model", "parallel_slots", int)),
        ("LOCALE_GATE_GPU_LAYERS", ("model", "gpu_layers", int)),
        ("LOCALE_GATE_ENABLE_THINKING", ("model", "enable_thinking", _parse_bool)),
        ("LOCALE_GATE_REQUEST_TIMEOUT_SECONDS", ("model", "request_timeout_seconds", float)),
        ("LOCALE_GATE_MAX_OUTPUT_TOKENS", ("model", "max_output_tokens", int)),
        ("LOCALE_GATE_STORE_RAW_USER_TEXT", ("logging", "store_raw_user_text", _parse_bool)),
        ("LOCALE_GATE_STORE_RAW_MODEL_OUTPUT", ("logging", "store_raw_model_output", _parse_bool)),
        (
            "LOCALE_GATE_EXPORT_ABSOLUTE_MODEL_PATH",
            ("logging", "export_absolute_model_path", _parse_bool),
        ),
    ]
    for env_key, (section, attr, converter) in env_map:
        raw_val = os.environ.get(env_key)
        if raw_val is not None and raw_val != "":
            section_obj = getattr(settings, section)
            setattr(section_obj, attr, converter(raw_val))

    settings.validate_security_posture()
    return settings


def _parse_bool(val: str) -> bool:
    return val.strip().lower() in {"1", "true", "yes", "on"}


_DEFAULT_SETTINGS: Settings | None = None


def get_settings() -> Settings:
    """Return cached default settings instance."""
    global _DEFAULT_SETTINGS
    if _DEFAULT_SETTINGS is None:
        _DEFAULT_SETTINGS = load_settings()
    return _DEFAULT_SETTINGS
