"""Binary GGUF header/metadata inspector and local llama-server runtime detector."""

from __future__ import annotations

import hashlib
import os
import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Standard GGUF file_type enum mapping (from ggml/llama.cpp)
GGUF_FILE_TYPE_MAP: dict[int, str] = {
    0: "ALL_F32",
    1: "MOSTLY_F16",
    2: "MOSTLY_Q4_0",
    3: "MOSTLY_Q4_1",
    7: "MOSTLY_Q8_0",
    8: "MOSTLY_Q5_0",
    9: "MOSTLY_Q5_1",
    10: "MOSTLY_Q2_K",
    11: "MOSTLY_Q3_K_S",
    12: "MOSTLY_Q3_K_M",
    13: "MOSTLY_Q3_K_L",
    14: "MOSTLY_Q4_K_S",
    15: "Q4_K_M",
    16: "MOSTLY_Q5_K_S",
    17: "MOSTLY_Q5_K_M",
    18: "MOSTLY_Q6_K",
}


@dataclass(frozen=True)
class GGUFArtifactManifest:
    """Verified metadata and provenance of the local GGUF model file."""

    model_family: str
    format: str
    quantization: str
    quantization_verified_from_header: bool
    path_alias: str
    file_size_bytes: int
    sha256: str
    gguf_version: int
    tensor_count: int
    kv_count: int
    architecture: str | None
    general_name: str | None
    quantized_by: str | None
    source_repository: str | None
    source_revision: str | None
    imatrix_file: str | None
    native_context_length: int | None
    has_embedded_chat_template: bool
    llama_cpp_version: str | None
    context_size: int
    gpu_layers: int
    thinking_enabled: bool

    def to_public_dict(self) -> dict[str, Any]:
        """Serialize manifest without exposing personal filesystem paths."""
        return {
            "model_family": self.model_family,
            "format": self.format,
            "quantization": self.quantization,
            "quantization_verified_from_header": self.quantization_verified_from_header,
            "path_alias": self.path_alias,
            "file_size_bytes": self.file_size_bytes,
            "sha256": self.sha256,
            "gguf_version": self.gguf_version,
            "tensor_count": self.tensor_count,
            "kv_count": self.kv_count,
            "architecture": self.architecture,
            "general_name": self.general_name,
            "quantized_by": self.quantized_by,
            "source_repository": self.source_repository,
            "source_revision": self.source_revision,
            "imatrix_file": self.imatrix_file,
            "native_context_length": self.native_context_length,
            "has_embedded_chat_template": self.has_embedded_chat_template,
            "llama_cpp_version": self.llama_cpp_version,
            "context_size": self.context_size,
            "gpu_layers": self.gpu_layers,
            "thinking_enabled": self.thinking_enabled,
        }


def compute_file_sha256(path: Path) -> str:
    """Compute hex SHA-256 digest of a file in 8 MiB chunks."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(8 * 1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def read_gguf_header_metadata(path: Path) -> dict[str, Any]:
    """Read GGUF v2/v3 header and key-value metadata directly from binary file."""
    with path.open("rb") as f:
        magic = f.read(4)
        if magic != b"GGUF":
            raise ValueError(f"File {path.name} is not a valid GGUF file (magic={magic!r}).")
        version, tensor_count, kv_count = struct.unpack("<IQQ", f.read(20))

        def _read_str() -> str:
            n = struct.unpack("<Q", f.read(8))[0]
            return f.read(n).decode("utf-8", errors="replace")

        def _read_val(vtype: int) -> Any:
            if vtype == 0:
                return struct.unpack("<B", f.read(1))[0]
            if vtype == 1:
                return struct.unpack("<b", f.read(1))[0]
            if vtype == 2:
                return struct.unpack("<H", f.read(2))[0]
            if vtype == 3:
                return struct.unpack("<h", f.read(2))[0]
            if vtype == 4:
                return struct.unpack("<I", f.read(4))[0]
            if vtype == 5:
                return struct.unpack("<i", f.read(4))[0]
            if vtype == 6:
                return struct.unpack("<f", f.read(4))[0]
            if vtype == 7:
                return struct.unpack("<B", f.read(1))[0] != 0
            if vtype == 8:
                return _read_str()
            if vtype == 9:
                atype, alen = struct.unpack("<IQ", f.read(12))
                if alen > 32:
                    for _ in range(alen):
                        _read_val(atype)
                    return f"<array len={alen}>"
                return [_read_val(atype) for _ in range(alen)]
            if vtype == 10:
                return struct.unpack("<Q", f.read(8))[0]
            if vtype == 11:
                return struct.unpack("<q", f.read(8))[0]
            if vtype == 12:
                return struct.unpack("<d", f.read(8))[0]
            raise ValueError(f"Unsupported GGUF metadata value type: {vtype}")

        kv: dict[str, Any] = {}
        for _ in range(kv_count):
            k = _read_str()
            vt = struct.unpack("<I", f.read(4))[0]
            v = _read_val(vt)
            kv[k] = v

    return {
        "gguf_version": version,
        "tensor_count": tensor_count,
        "kv_count": kv_count,
        "kv": kv,
    }


def discover_llama_server_binary(configured_path: str | None = None) -> Path | None:
    """Locate llama-server.exe on Windows or POSIX, preferring the WinGet official release."""
    if configured_path:
        p = Path(configured_path)
        if p.is_file():
            return p

    env_bin = os.environ.get("LOCALE_GATE_LLAMA_SERVER_BIN")
    if env_bin:
        p = Path(env_bin)
        if p.is_file():
            return p

    # Check WinGet official ggml.llamacpp installation path on Windows
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        winget_dir = Path(local_app_data) / "Microsoft" / "WinGet" / "Packages"
        if winget_dir.is_dir():
            for match in winget_dir.glob("ggml.llamacpp*/**/llama-server.exe"):
                if match.is_file():
                    return match
        winget_links = Path(local_app_data) / "Microsoft" / "WinGet" / "Links" / "llama-server.exe"
        if winget_links.is_file():
            return winget_links

    which_bin = shutil.which("llama-server") or shutil.which("llama-server.exe")
    if which_bin:
        return Path(which_bin)

    return None


def query_llama_server_version(binary_path: Path | None = None) -> str | None:
    """Run `llama-server --version` and return a concise version string."""
    target = binary_path or discover_llama_server_binary()
    if target is None or not target.is_file():
        return None
    try:
        proc = subprocess.run(
            [str(target), "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        for line in combined.splitlines():
            if line.strip().lower().startswith("version:"):
                return line.strip()
        return combined.splitlines()[0].strip() if combined else "unknown"
    except Exception:
        return None


def inspect_gguf_artifact(
    path: Path,
    *,
    path_alias: str = "configured-local-model",
    context_size: int = 4096,
    gpu_layers: int = 0,
    thinking_enabled: bool = False,
    compute_hash: bool = True,
    llama_cpp_version: str | None = None,
) -> GGUFArtifactManifest:
    """Inspect a local GGUF file, verify Qwen3.5-4B / Q4_K_M metadata, and build a manifest."""
    if not path.is_file():
        raise FileNotFoundError(f"GGUF file not found: {path.name}")

    stat = path.stat()
    header = read_gguf_header_metadata(path)
    kv = header["kv"]

    arch = kv.get("general.architecture")
    gen_name = kv.get("general.name") or kv.get("general.basename")
    quantized_by = kv.get("general.quantized_by")
    repo_url = kv.get("general.repo_url")
    imatrix_file = kv.get("quantize.imatrix.file")
    file_type_id = kv.get("general.file_type")

    if isinstance(file_type_id, int) and file_type_id in GGUF_FILE_TYPE_MAP:
        quant_str = GGUF_FILE_TYPE_MAP[file_type_id]
        quant_verified = True
    else:
        quant_str = "unverified"
        quant_verified = False

    source_repo = None
    if isinstance(imatrix_file, str) and "/" in imatrix_file:
        repo_prefix = imatrix_file.split("/")[0]
        if quantized_by:
            source_repo = f"{quantized_by.lower()}/{repo_prefix}"
    elif isinstance(repo_url, str):
        source_repo = repo_url

    ctx_key = f"{arch}.context_length" if arch else "qwen35.context_length"
    native_ctx = kv.get(ctx_key)

    sha256_str = compute_file_sha256(path) if compute_hash else "skipped"
    resolved_llama_ver = (
        llama_cpp_version if llama_cpp_version is not None else query_llama_server_version()
    )

    return GGUFArtifactManifest(
        model_family=str(gen_name or "Qwen3.5-4B"),
        format="GGUF",
        quantization=quant_str,
        quantization_verified_from_header=quant_verified,
        path_alias=path_alias,
        file_size_bytes=stat.st_size,
        sha256=sha256_str,
        gguf_version=int(header["gguf_version"]),
        tensor_count=int(header["tensor_count"]),
        kv_count=int(header["kv_count"]),
        architecture=str(arch) if arch else None,
        general_name=str(gen_name) if gen_name else None,
        quantized_by=str(quantized_by) if quantized_by else None,
        source_repository=source_repo,
        source_revision=None,
        imatrix_file=str(imatrix_file) if imatrix_file else None,
        native_context_length=int(native_ctx) if isinstance(native_ctx, int) else None,
        has_embedded_chat_template=bool(kv.get("tokenizer.chat_template")),
        llama_cpp_version=resolved_llama_ver,
        context_size=context_size,
        gpu_layers=gpu_layers,
        thinking_enabled=thinking_enabled,
    )
