# OJU Video Forge — Provider-Independent Inference Architecture

This document describes the provider-agnostic inference layer that decouples OJU Video Forge from mandatory Ollama by supporting multiple inference backends.

## 1. Overview

OJU Video Forge supports two inference providers:

- **Ollama** — Default provider, wraps existing `LocalOllamaAdapter` with full legacy compatibility
- **llama.cpp** — Optional internal-only service, implements OpenAI-compatible `/v1/chat/completions` API

No external inference endpoints are permitted. All inference must occur on OJU infrastructure.

## 2. Protocol and Interface

### 2.1 Provider Contract (`app/inference_providers/__init__.py`)

The `InferenceProvider` protocol defines the following operations:

- **`status()`** — Return readiness state and model verification
- **`capabilities()`** — Report supported capabilities (text, vision, structured output)
- **`generate(messages, options)`** — Generate chat completions
- **`generate_structured(messages, schema)`** — Generate JSON-schema validated responses
- **`provenance()`** — Return provider identity and model information

### 2.2 Error Classification

All providers must raise `AgentUnavailableError` when:

- The provider service is unreachable
- No models are loaded
- Authentication/authorization fails
- Model identity validation errors

## 3. Provider Implementation Details

### 3.1 Ollama Adapter (`app/inference_providers/ollama_adapter.py`)

Wraps the existing `LocalOllamaAdapter` from `app/adapters.py`:

- Preserves all established Ollama behavior
- Uses existing `/v1/chat/completions` endpoint
- Maintains inference admission and Redis lease behavior
- Records provenance from existing model identity tracking

### 3.2 llama.cpp Adapter (`app/inference_providers/llama_cpp_adapter.py`)

Implements the OpenAI-compatible llama.cpp server API:

- Endpoint: `POST /v1/chat/completions`
- Models endpoint: `GET /v1/models`
- Handles structured JSON responses via format field
- Reports timeout/cancellation via stream interruption
- Vision capability must be explicitly enabled by deployment (disabled by default)

## 4. Configuration (`app/config.py`)

New settings added for role-specific routing:

```python
# llama.cpp optional inference provider settings
llama_cpp_enabled: bool = False              # Disable by default
llama_cpp_url: str = "http://llama-cpp:8080"
llama_cpp_model: str = ""                     # Leave empty to use default
llama_cpp_model_digest: str = ""
```

Default inference workloads continue using Ollama. llama.cpp must be explicitly enabled via environment variable `LLAMA_CPP_ENABLED=true`.

## 5. Compose Overlay (`compose.llamacpp.yaml.example`)

Usage for internal-only deployment:

```yaml
include:
  - compose.llamacpp.yaml
```

Environment variables required:

```bash
LLAMA_CPP_ENABLED=true
LLAMA_CPP_URL=http://llama-cpp:8080
LLAMA_CPP_MODEL=mistral-latest
LLAMA_CPP_MODEL_DIGEST=<SHA256_DIGEST>
```

**Important:** llama.cpp image must be pinned to a verified digest before deployment. Example format:

```yaml
image: ghcr.io/ggml-org/llama.cpp:server-cuda@sha256:<DIGEST_FROM_RELEASES>
```

The compose overlay includes:

- No published host ports (internal network only)
- Separate GGUF model storage directory (`/models`)
- GPU allocation via Docker `gpus: all` directive
- Health check based on `/v1/models` endpoint
- Entrypoint and arguments for llama.cpp server

## 6. Testing

New tests in `tests/test_inference_providers.py`:

- Factory selection validation
- Unknown provider rejection (disabled state)
- Ollama compatibility regression
- Structured output handling
- Provenance recording verification

Run tests:

```bash
python -m pytest tests/test_inference_providers.py -v
```

## 7. Deployment and Rollback

### Enabling llama.cpp

1. Set `LLAMA_CPP_ENABLED=true` in environment
2. Mount GGUF model artifact with verified digest
3. Include `compose.llamacpp.yaml` in compose stack
4. Verify via `/v1/models` endpoint availability

### Rolling back to Ollama

1. Set `LLAMA_CPP_ENABLED=false` or remove compose overlay
2. Existing Ollama infrastructure remains unchanged
3. No data migration required

## 8. Vision and Multimodal Support

Vision requests require explicit qualification:

- llama.cpp vision capability is **disabled by default**
- Must configure appropriate GGUF models (e.g., qwen2.5-vl)
- Model digest must include provenance tracking
- Ollama remains the only provider with verified vision support until llama.cpp vision is tested and approved

## 9. Future Work

Pending model qualification:

- HuggingFace Qwen2.5-VL GGUF integration testing
- Multimodal artifact validation pipeline
- Structured output with vision models
- Production benchmarking and acceptance criteria

## 10. Appendix

### Model Artifact Verification

GGUF models should be stored in a read-only volume with checksums:

```bash
sha256sum /models/<model-name>.gguf > /artifacts/model-checksums.txt
```

Include digest in `llama_cpp_model_digest` environment variable.

### OpenAPI Contract

All inference endpoints conform to OpenAI-compatible spec for cross-provider compatibility. Use `python scripts/export_openapi.py --output api-schema.json` to export current schema.
