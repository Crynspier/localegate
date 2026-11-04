"""Model integration and GGUF inspection package."""

from locale_gate.models.adapter import (
    DeterministicMockAdapter,
    ModelAdapter,
    ModelCallTelemetry,
    ModelProposalOutput,
    OpenAICompatibleLocalAdapter,
    build_user_prompt_payload,
    load_system_prompt,
    run_model_preflight_smoke_test,
)
from locale_gate.models.gguf_inspector import (
    GGUF_FILE_TYPE_MAP,
    GGUFArtifactManifest,
    compute_file_sha256,
    discover_llama_server_binary,
    inspect_gguf_artifact,
    query_llama_server_version,
    read_gguf_header_metadata,
)

__all__ = [
    "GGUF_FILE_TYPE_MAP",
    "DeterministicMockAdapter",
    "GGUFArtifactManifest",
    "ModelAdapter",
    "ModelCallTelemetry",
    "ModelProposalOutput",
    "OpenAICompatibleLocalAdapter",
    "build_user_prompt_payload",
    "compute_file_sha256",
    "discover_llama_server_binary",
    "inspect_gguf_artifact",
    "load_system_prompt",
    "query_llama_server_version",
    "read_gguf_header_metadata",
    "run_model_preflight_smoke_test",
]
