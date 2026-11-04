# Local Model Setup on Windows (`llama.cpp` + `Qwen3.5-4B-Q4_K_M.gguf`)

## Verified Local Model Artifact

- **File:** `Qwen3.5-4B-Q4_K_M.gguf`
- **Size:** `2,740,937,888` bytes
- **SHA-256:** `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`
- **GGUF Header Metadata:** Version `3`, `426` tensors, `general.architecture = qwen35`, `general.name = Qwen3.5-4B`, `general.file_type = 15` (`Q4_K_M`), quantized by `Unsloth` (`Qwen3.5-4B-GGUF/imatrix_unsloth.gguf`), native context length `262144`, embedded `tokenizer.chat_template`.

## Installing `llama-server.exe` on Windows

Because `Qwen3.5-4B` uses the `qwen35` GGUF architecture, a recent `llama.cpp` build is required. Install the official `ggml.llamacpp` package via WinGet:

```powershell
winget install ggml.llamacpp --accept-package-agreements --accept-source-agreements
```

Verify the binary and model header using the LocaleGate CLI:

```powershell
uv run locale-gate doctor
uv run locale-gate model inspect --compute-hash
```

## Starting the Local Loopback Inference Server

Run `llama-server.exe` bound strictly to `127.0.0.1:8080` with `--jinja` enabled and a 4096-token context window suitable for 16 GB RAM:

```powershell
llama-server `
  --model ".\Qwen3.5-4B-Q4_K_M.gguf" `
  --alias "local-qwen35-4b-q4km" `
  --host 127.0.0.1 `
  --port 8080 `
  --ctx-size 4096 `
  --parallel 1 `
  --n-gpu-layers 0 `
  --jinja
```

Then run the 5-case mandatory preflight check:

```powershell
uv run locale-gate model smoke-test
```
