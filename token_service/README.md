# Token Service

Issues short-lived, write-only SAS URLs for log uploads.

This service is unchanged by the proof-of-concept repair direction. It still only handles least-privilege upload authorization for node log batches.

## How It Works

1. Validate `x-node-id` and `x-api-key` against the request body.
2. Read the storage account key from Key Vault.
3. Generate a unique blob path under `logs/<node_id>/<uuid>`.
4. Build a SAS URL that only grants write access to that one blob.

## Modules

- `auth.py`: request authentication and node identity checks
- `config.py`: environment-backed configuration
- `keyvault.py`: Managed Identity secret lookup
- `sas_generator.py`: pure blob-path and SAS-token construction helpers
- `app.py`: request parsing and orchestration
- `main.py`: Azure Function entrypoint and local smoke-test CLI

## Tests

```bash
bash scripts/test.sh
```
