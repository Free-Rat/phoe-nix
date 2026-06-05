# Analysis Agent

Consumes normalized logs or local observations from Service Bus topic `analysis-input`, builds an AI prompt, calls the OpenCode API, and publishes analysis output to `analysis-results`.

The current implementation still validates structured `AnalysisResult` payloads, but the intended proof-of-concept direction allows more raw analysis text so `local_agent` can interpret the problem locally. See `../proof-of-concept-direction.md`.

## Modules

- `config.py`: environment-backed runtime configuration
- `keyvault.py`: Key Vault secret lookup through Managed Identity
- `prompt_builder.py`: pure prompt construction functions for each message type
- `ai_client.py`: pure request/response helpers plus OpenCode HTTP adapter
- `message_handler.py`: orchestration for parse -> prompt -> model -> validated schema
- `main.py`: Azure Function entrypoint and Service Bus publisher

## Message Flow

1. Read message from `analysis` topic.
2. Detect whether the payload is a `NormalizedLog` or `Observation`.
3. Build a source-specific prompt.
4. Fetch the OpenCode API key from Key Vault.
5. Call the OpenCode API.
6. Validate and enrich the AI output into `AnalysisResult` in the current implementation, or emit richer diagnosis text in the proof-of-concept direction.
7. Publish the result to the `analysis-results` topic.

## Tests

Run from repo root:

```bash
bash scripts/test.sh
```
