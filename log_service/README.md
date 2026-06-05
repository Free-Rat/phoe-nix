# Log Service

Runs on a NixOS node, tails systemd journal entries, batches them, uploads them to Blob Storage, and spools failed batches locally for retry.

In the proof-of-concept direction, these uploaded logs are one of the evidence streams that eventually drive local config-repair attempts by `local_agent`. See `../proof-of-concept-direction.md`.

## How It Works

1. Subscribe to journal entries, optionally filtered by unit names from CLI args.
2. Buffer entries in memory.
3. Flush when the batch size limit is reached, when the flush interval elapses, or during shutdown.
4. For each flush, request a fresh SAS URL from `token_service`.
5. Upload the batch payload to Blob Storage.
6. If upload retries are exhausted, write the batch to the spool directory and replay it on the next flush.

## Modules

- `config.py`: runtime configuration including batch and retry settings
- `token_client.py`: Token Service HTTP client
- `storage.py`: batch payload serialization and blob upload adapter
- `uploader.py`: batching, retry, and spooling logic
- `main.py`: journal loop and signal handling

## Usage

Preferred service commands from this repo:

```bash
nix develop
log_service -s nginx
```

Repo-level test command:

```bash
bash scripts/test.sh
```

## Note

The service logic and tests are implemented, but the existing `nix run` packaging path still needs follow-up cleanup in `flake.nix`.
