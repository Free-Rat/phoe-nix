# Local Deployment Simulator

Simulates a realistic end-to-end deployment of the implemented pipeline without Azure infrastructure.

## What It Simulates

- Key Vault secret lookup
- Token Service request/response flow
- Blob upload from `log_service`
- Blob-trigger style routing by `log_router`
- Service Bus topics representing analysis input, analysis output, and final decisions
- OpenCode API responses
- Cosmos DB decision audit writes
- Local-agent decision consumption and execution-result storage

## Why This Is Useful

The repo now has enough cloud-side components that unit tests alone are not enough. This simulator exercises the real service core logic in deployment order:

1. `token_service`
2. `log_service`
3. `log_router`
4. `analysis_agent`
5. `decision_agent`
6. simulated `local_agent` execution

## Included Scenarios

- log-ingestion happy path
- observation-only happy path
- token-service failure with local spooling
- blob-upload retry and recovery
- malformed uploaded log blob
- invalid AI response from OpenCode
- future local-agent config-repair attempts driven by decision plus analysis context

## Run

From the repo root:

```bash
bash scripts/simulate-deployment.sh
```

This prints a JSON summary containing uploaded blob paths, topic messages, and Cosmos audit documents.

## Test

```bash
bash scripts/test.sh
```
