# Log Service (on node)

## responsibility

    - Gets a token from Upload Authorization Service
    - Collects logs from NixOS nodes
    - Pushes raw logs to Azure Blob Storage

## implementation

    - we callect journal logs for servicies specified in args

## usage

### running with nix run (no shell needed)

```bash
# run directly
nix run .

# with options
nix run . -- -s nginx systemd
```

### running with nix develop

```bash
# enter nix shell with dependencies
nix develop

# run as module
python -m log_service

# or install and run
pip install -e .
log-service -s nginx
```

### options

```
-s, --services    Filter logs by service name(s). If not specified, all logs are shown.
-h, --help        Show help message
```

### examples

```bash
# show all logs
nix run . 

# filter by single service
nix run . -- -s nginx

# filter by multiple services
nix run . -- -s nginx systemd docker

# full option syntax
nix run . -- --services sshd
```

### shutdown

The service handles `SIGINT` (Ctrl+C) and `SIGTERM` gracefully, cleaning up before exit.
