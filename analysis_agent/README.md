# Analysis Agent

Azure Function that consumes Service Bus messages from `analysis-input` (subscription `analysis-agent`), calls OpenCode, and publishes a JSON `AnalysisResult` to `analysis-results`.

## Inputs

- `schemas.NormalizedLog` messages from `log_router`
- `schemas.Observation` messages from `local_agent`

The handler uses the payload's `source` field to choose the schema: `local_agent` is parsed as `Observation`; everything else is treated as `NormalizedLog`.

## Output

- one Service Bus message per input message
- body: `schemas.AnalysisResult` JSON
- `content_type=application/json`
- `application_properties.message_kind=analysis_result`
- `message_id` preserved from the original input message when available

If the model response is not valid JSON, the parser falls back to a text-derived `AnalysisResult` so the pipeline still produces a usable result.

## Runtime and configuration

This package runs inside the Azure Functions host (`src/analysis_agent/main.py` plus `function.json`); it is not a standalone daemon.

Required settings:

- `SERVICEBUS_CONNECTION`
- `KEYVAULT_NAME`

Function binding and AI defaults:

- `SERVICEBUS_TOPIC_ANALYSIS_INPUT_NAME` (default `analysis-input`)
- `SERVICEBUS_TOPIC_ANALYSIS_RESULTS_NAME` (default `analysis-results`)
- `OPENCODE_API_KEY_SECRET` (default `OpenCodeApiKey`)
- `OPENCODE_API_URL` (default `https://opencode.ai/zen/go/v1/chat/completions`)
- `OPENCODE_MODEL` (default `deepseek-v4-flash`)
- `AI_TIMEOUT_SECONDS` (default `30`)

The OpenCode API key is read from Key Vault using `DefaultAzureCredential`.

## Key files

- `config.py`: environment-backed runtime config
- `keyvault.py`: Key Vault secret lookup
- `prompt_builder.py`: prompt construction for log and observation inputs
- `ai_client.py`: OpenCode request/response helpers
- `message_handler.py`: parse -> prompt -> model -> result pipeline
- `main.py`: Azure Function entrypoint and Service Bus publisher

## Local setup and validation

For interactive work, use the package Nix shell:

```bash
cd analysis_agent && nix develop
```

Repo-wide validation runs from the repository root:

```bash
bash scripts/test.sh
```

## Planned direction

The proof-of-concept direction keeps `analysis_agent` text-forward, but the current implementation still emits structured `AnalysisResult` output. See `../proof-of-concept-direction.md`.
