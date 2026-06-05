# Decision Agent

Consumes analysis output from `analysis-results`, turns it into remediation intent for a node, stores each decision in Cosmos DB for audit, and publishes the final `Decision` payload to `final-decisions`.

The current implementation is still command-oriented. The intended proof-of-concept direction is looser: the decision should give `local_agent` enough remediation context to attempt config-level self-repair. See `../proof-of-concept-direction.md`.

## Modules

- `config.py`: environment-backed runtime configuration
- `decision_engine.py`: pure command mapping and idempotency logic
- `cosmos.py`: Cosmos DB upsert adapter using Managed Identity
- `app.py`: orchestration for build -> store
- `main.py`: Azure Function entrypoint and Service Bus publisher

## Current Decision Rules

- `rollback` -> `nixos-rebuild switch --rollback`
- `rebuild` -> `nixos-rebuild switch`
- `restart_service` -> `systemctl restart <unit>`
- `no_action` -> empty command for audit-only decisions

For the proof of concept, these rules are expected to evolve toward text-forward remediation intent rather than only concrete shell commands.

## Tests

Run from repo root:

```bash
bash scripts/test.sh
```
