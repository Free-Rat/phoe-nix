# Log Router

Normalizes uploaded log batches and forwards them to Service Bus topic `analysis-input`.

## How It Works

1. Blob trigger reads a batch uploaded by `log_service`.
2. `normalizer.py` parses the batch payload and converts each raw journal entry into `schemas.NormalizedLog`.
3. `main.py` publishes one message per normalized entry to Service Bus.

## Modules

- `normalizer.py`: pure parsing and normalization helpers
- `main.py`: Azure Function entrypoint and Service Bus publisher

## Tests

```bash
bash scripts/test.sh
```
