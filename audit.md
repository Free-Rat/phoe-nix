# Phoe-nix Code Audit

Date: 2026-06-12

Scope: repository-wide audit against `README.md`, `current-state.md`, `proof-of-concept-direction.md`, and `PLAN.md`. This review focused on whether the implemented cloud pipeline and local Git-backed repair loop actually match the intended proof-of-concept behavior, plus correctness, security, reliability, and design risks.

## Executive Summary

The repository has a coherent POC shape, but several implementation details undermine the core promise that `local_agent` edits the config repo, validates that exact config, applies it, and reports trustworthy results.

The highest-priority problems are:

- The repair loop edits `CONFIG_REPO_PATH/configuration.nix` but runs plain `nixos-rebuild test` and `nixos-rebuild switch`, which normally target `/etc/nixos/configuration.nix`, not the edited repo file.
- The legacy direct-command decision path still allows externally supplied Service Bus decisions to execute arbitrary shell strings with `shell=True`.
- AI-derived `affected_unit` is interpolated into shell commands without validation, creating a prompt-injection-to-command-injection path.
- `apply_config` is recognized as an action but can still crash `decision_agent` if the AI response does not include text matching a Nix assignment regex.
- Local-agent Service Bus receive/complete handling likely settles messages on a different receiver than the one that received them, making duplicate execution or failed completion likely.
- Idempotency keys are generated but never enforced by `local_agent`.
- Successful repairs synthesize a healthy node state instead of collecting real post-repair state.

The POC intentionally deprioritizes production safety. Even with that constraint, the issues above affect the demo's correctness and traceability, not just hardening.

## Critical Issues

### 1. Core repair loop likely validates and applies the wrong NixOS config

References:

- `local_agent/src/local_agent/config.py:36-46`
- `local_agent/src/local_agent/repair_planner.py:115-134`
- `local_agent/src/local_agent/repair_planner.py:150-180`
- `infrastructure/render-vm-env.sh:150-152`
- `proof-of-concept-direction.md:15-18`

The intended POC flow is to edit `configuration.nix` in `https://github.com/Free-Rat/phoe-nix-config`, run `nixos-rebuild test`, then `nixos-rebuild switch`. The implementation writes the proposed config to `repo_path/config_file_path`, but the default commands are plain `nixos-rebuild test` and `nixos-rebuild switch`.

Plain `nixos-rebuild` normally reads `/etc/nixos/configuration.nix`, unless given `-I nixos-config=...` or a flake target. The rendered VM env sets `CONFIG_REPO_PATH=/var/lib/phoe-nix-config-repo`, so the edited file is especially unlikely to be the default NixOS config path.

Impact: the agent can test and switch a different configuration than the one it just edited, then commit and push an unvalidated config. This breaks the central POC behavior.

Suggested fix: make the rebuild target explicit. For non-flake config, set default commands like `nixos-rebuild test -I nixos-config=$CONFIG_REPO_PATH/$CONFIG_FILE_PATH` and the matching `switch`. If the config repo is a flake, use `nixos-rebuild test --flake "$CONFIG_REPO_PATH#<host>"` and persist the exact target in repair traces.

### 2. Remote command execution through Service Bus decisions and `shell=True`

References:

- `schemas/src/schemas/decision.py:12-13`
- `local_agent/src/local_agent/runtime.py:165-218`
- `local_agent/src/local_agent/executor.py:22-33`
- `decision_agent/src/decision_agent/decision_engine.py:47-54`

`Decision.command` is accepted from the `final-decisions` payload. If it is non-empty, `local_agent` executes it through `execute_decision()`, which ultimately calls `subprocess.run(command, shell=True, ...)`.

The only target check is `decision.node_id == local_node_id`. Anyone who can publish to `final-decisions`, compromise a service using the shared Service Bus connection, or induce the cloud path to emit a malicious command can execute arbitrary shell commands on the node.

Impact: high-confidence node-level RCE with the local agent's privileges.

Suggested fix: remove arbitrary command execution from externally received decisions. Use a closed action enum plus structured parameters. Execute argv arrays, not shell strings. Gate the legacy direct-command path behind an explicit unsafe debug flag if it must remain for demos.

### 3. Prompt injection can become command injection through `affected_unit`

References:

- `analysis_agent/src/analysis_agent/ai_client.py:137-190`
- `decision_agent/src/decision_agent/decision_engine.py:51-54`
- `local_agent/src/local_agent/executor.py:30-33`
- `schemas/src/schemas/analysis_result.py:23-24`

Untrusted logs are analyzed by an LLM. The resulting `affected_unit` can be interpolated directly into `systemctl restart {affected_unit}` and later executed with `shell=True`.

A malicious log or prompt-injected model response could set `affected_unit` to a value such as `nginx.service; curl ... | sh`.

Impact: prompt-injection-to-RCE path on the local agent host.

Suggested fix: treat AI output as untrusted. Validate unit names against a strict allowlist or regex such as `^[A-Za-z0-9_.@:-]+\.service$`, and run `systemctl` through argv form: `['systemctl', 'restart', unit]`.

### 4. `apply_config` decisions can fail before reaching `local_agent`

References:

- `decision_agent/src/decision_agent/decision_engine.py:12-24`
- `decision_agent/src/decision_agent/decision_engine.py:41-57`
- `decision_agent/src/decision_agent/decision_engine.py:77-90`

`normalize_suggested_action()` recognizes `apply_config`, but `build_command()` has no `apply_config` branch. It returns an empty command only when `analysis_text` or `remediation_hint` matches `NIX_ASSIGNMENT_PATTERN`; otherwise it raises `ValueError("unsupported action: apply_config")`.

Impact: a valid intent-forward cloud decision, such as `suggested_action=apply_config` with prose remediation context, can crash `decision_agent` and prevent the repair loop from running.

Suggested fix: make `apply_config` first-class and command-free. `build_command()` should return `""` for normalized `apply_config` regardless of whether a regex detects a Nix assignment.

### 5. Service Bus decisions are received and completed on different receivers

References:

- `local_agent/src/local_agent/bus_client.py:49-68`
- `local_agent/src/local_agent/bus_client.py:71-86`
- `local_agent/src/local_agent/runtime.py:451-480`

`receive_messages()` opens a Service Bus client and receiver in a `with` block, receives messages, returns them, and closes the receiver. `complete_message()` later opens a new receiver and attempts to complete the old message.

Azure Service Bus message settlement is tied to the receiver/link that received the locked message. Settling with another receiver is unreliable and can fail.

Impact: decisions can be redelivered after lock expiry, causing duplicate repairs, duplicate rebuilds, duplicate Git commits, or DLQ behavior. The current simulator does not model this lifecycle, so this issue is easy to miss.

Suggested fix: keep a receiver open across receive, process, and complete/abandon/dead-letter. `decision_worker()` should own the receiver context and settle messages on the same receiver instance.

## Likely Bugs

### 6. Idempotency key is generated but never enforced

References:

- `schemas/src/schemas/decision.py:18`
- `decision_agent/src/decision_agent/decision_engine.py:60-66`
- `local_agent/src/local_agent/runtime.py:165-319`

`Decision.idempotency_key` exists and is computed from the analysis, but `local_agent` never checks or records processed keys. `execution_id` and `decision_id` are not sufficient because retries or repeated analyses can produce distinct IDs.

Impact: duplicate Service Bus delivery, function retries, or repeated analysis of the same incident can re-run the same repair.

Suggested fix: store processed `node_id + idempotency_key` records in Cosmos or local durable state. Claim before execution with conditional create semantics, mark complete after success, and skip or resume duplicates.

### 7. Successful repairs report synthetic healthy state

References:

- `local_agent/src/local_agent/runtime.py:258-271`
- `local_agent/src/local_agent/executor.py:72-76`
- `local_agent/src/local_agent/reporter.py:8-11`
- `proof-of-concept-direction.md:19,44-55`

After a successful repair, the code sets `NodeState(failed_units=[])` instead of collecting live system state. Direct command execution does the same by default.

Impact: reports can claim "No failed units remain" even if the service still fails, the switch did not affect the intended service, or a different problem remains.

Suggested fix: call `runtime.dependencies.read_node_state()` after repair or command execution and persist that actual state. If collection fails, record an explicit `unknown` or degraded post-state instead of a healthy one.

### 8. All nodes share one unfiltered `final-decisions` subscription

References:

- `infrastructure/04-stateless/main.tf:68-74`
- `infrastructure/render-vm-env.sh:139-143`
- `local_agent/src/local_agent/runtime.py:178-185`
- `local_agent/src/local_agent/runtime.py:451-465`

Terraform creates a single `local-agent` subscription on `final-decisions`, and the rendered VM env points every VM at that same subscription. Multiple VMs using one subscription become competing consumers. If node B receives a decision for node A, `handle_decision()` returns an error, but `decision_worker()` increments `processed` and completes the message.

Impact: in any multi-node run, decisions can be consumed by the wrong node and lost before the intended node sees them.

Suggested fix: provision one subscription per node with a SQL filter on `node_id`, or put `node_id` in Service Bus application properties and use filtered subscriptions. Wrong-node messages should not be completed on a shared subscription.

### 9. Repair loop can leave repo, pushed state, and running system inconsistent

References:

- `local_agent/src/local_agent/repair_planner.py:133-148`
- `local_agent/src/local_agent/repair_planner.py:150-209`
- `local_agent/src/local_agent/git_repo.py:37-47`

Failed `nixos-rebuild test` attempts leave the proposed bad config in the working tree and use it as the next attempt's base. If `nixos-rebuild switch` succeeds but `git push` fails, the running VM may use an unpushed config. The loop then calls `refresh_repo()`, which performs `git reset --hard origin/<branch>` and `git clean -fd`, discarding local repo evidence while the system may remain switched to that discarded config.

Impact: the running generation, local repo, remote repo, and Cosmos repair trace can diverge.

Suggested fix: use a transactional lifecycle. Work on a branch or worktree, validate the exact candidate, commit before switch where possible, push before or immediately after switch with explicit local-only state on failure, and always persist active NixOS generation plus Git revision.

### 10. Remediation safety state is in-memory only and the hourly cap never resets

References:

- `local_agent/src/local_agent/runtime.py:64-66`
- `local_agent/src/local_agent/state.py:11-17`
- `local_agent/src/local_agent/state.py:49-70`

`LocalAgentRuntime` always starts with `new_state()`. Cooldown, last observation hash, and remediation count are not loaded from durable state. `remediations_this_hour` increments forever and is never reset based on time.

Impact: restart forgets safety limits and can reapply remediations; a long-running daemon eventually blocks permanently after `max_remediations_per_hour` repairs.

Suggested fix: persist local safety state and use a sliding window of remediation timestamps rather than a monotonically increasing counter.

### 11. Rendered VM env omits required `NODE_ID` for `log_service`

References:

- `log_service/src/log_service/config.py:18-23`
- `infrastructure/render-vm-env.sh:136-137`

`LogServiceConfig` requires `NODE_ID`, but `render-vm-env.sh` writes only `TOKEN_SERVICE_URL` and `NODE_API_KEY` to `log-service.env`.

Impact: a VM using the rendered env file will fail log-service startup with a missing environment variable, breaking the log ingestion path.

Suggested fix: include `NODE_ID=${NODE_ID:-nixos}` or require a node id argument in `render-vm-env.sh` and render it into both log-service and local-agent env files.

### 12. Rendered Ollama model does not match the code default

References:

- `local_agent/src/local_agent/config.py:41-42`
- `infrastructure/render-vm-env.sh:154-155`

The code default is `gemma3:4b`, but the rendered env sets `OLLAMA_MODEL=gemma4:e4b`, which appears inconsistent and likely invalid unless a custom local model exists.

Impact: fresh VM repair runs may fail at local LLM generation even when the code default would work.

Suggested fix: align the rendered model with a tested tag or make the model an explicit required option in the render script.

### 13. Local hardware config can be required for rebuild but never committed

References:

- `local_agent/src/local_agent/repair_planner.py:85-91`
- `local_agent/src/local_agent/git_repo.py:75-86`

The repair loop copies `/etc/nixos/hardware-configuration.nix` into the repo if missing, but `commit_and_push()` only stages `config_file_path`, usually `configuration.nix`.

Impact: a local rebuild may pass because the hardware file exists locally, while the pushed repo remains incomplete or broken for other clones.

Suggested fix: commit all intended support files or explicitly include `hardware-configuration.nix` when it is created. Alternatively, keep hardware config out of the shared repo and ensure the rebuild command imports the node-local hardware file intentionally.

### 14. Analysis parsing allows AI output to override trusted envelope fields

References:

- `analysis_agent/src/analysis_agent/ai_client.py:179-188`

`parse_analysis_response()` uses `payload.setdefault()` for `node_id`, `original_message_id`, `source_type`, and `affected_unit`. If the model returns these fields, the model value wins over the trusted input message value.

Impact: a hallucinated or prompt-injected model response can redirect analysis and decisions to the wrong node or source message.

Suggested fix: always overwrite trusted envelope fields from the input after parsing model output. Only accept diagnosis/remediation fields from the model.

### 15. One malformed journal entry drops the whole log blob

References:

- `log_router/src/log_router/normalizer.py:29-49`

`normalize_blob()` normalizes all entries through a list comprehension. If one entry is missing `MESSAGE`, has a bad timestamp, or has an invalid priority, the whole batch raises and no valid entries are published.

Impact: one malformed entry can block a full blob, trigger retries, and hide useful logs from analysis.

Suggested fix: normalize per entry, publish valid entries, and emit structured error records for invalid entries with blob path and entry index.

### 16. Blob lifecycle prefix likely does not match actual blob names

References:

- `token_service/src/token_service/sas_generator.py:10-15`
- `token_service/src/token_service/sas_generator.py:53-66`
- `infrastructure/03-blob-storage/main.tf:25-45`

Blobs are stored in container `logs` with names like `<node_id>/<uuid>`. The storage management policy filters with `prefix_match = ["logs/"]`. In Azure lifecycle policies, prefixes are evaluated against blob names or container/blob prefixes depending on API shape, and this configuration is likely not matching blobs whose names do not start with `logs/` inside the `logs` container.

Impact: uploaded logs may not be cleaned up as intended.

Suggested fix: verify the exact Azure provider semantics. If scoped to blob names inside the container, remove the prefix or use real node prefixes. Add an infra validation note/test.

## Conceptual And Design Problems

### 17. Cloud-side direct commands contradict the intended local-agent-centered repair model

References:

- `proof-of-concept-direction.md:9-19`
- `decision_agent/src/decision_agent/decision_engine.py:41-57`
- `local_agent/src/local_agent/runtime.py:196-220`
- `local_agent/src/local_agent/executor.py:30-33`

The POC direction says cloud decisions should provide context while `local_agent` owns the repair. The implementation still lets `decision_agent` prescribe direct privileged commands: rollback, rebuild, and service restart.

This is both a design mismatch and a security issue. It also bypasses the richer Git-backed traceability path.

Suggested fix: make `apply_config` the default and only mutating action in POC mode. Treat legacy direct commands as disabled-by-default maintenance tools.

### 18. Service Bus authorization is namespace-wide and overprivileged

References:

- `infrastructure/04-stateless/main.tf:76-83`
- `infrastructure/04-stateless/main.tf:141-144`
- `infrastructure/04-stateless/main.tf:259-263`
- `infrastructure/04-stateless/main.tf:295-299`
- `infrastructure/04-stateless/main.tf:334-338`

One namespace-level Shared Access Policy has both `listen` and `send`, and it is reused across router, analysis, decision, and local-agent flows.

Impact: compromise of any holder can publish forged analysis results or final decisions. Combined with direct command execution, this becomes node RCE.

Suggested fix: use least-privilege credentials per component. Functions should prefer managed identity. Local agents should receive only from their own filtered subscription and publish only observations, not decisions. Consider signing decisions.

### 19. Node authentication and SAS issuance use one shared key and self-asserted identity

References:

- `token_service/src/token_service/auth.py:20-27`
- `token_service/src/token_service/models.py:6-7`
- `token_service/src/token_service/sas_generator.py:10-11`
- `infrastructure/04-stateless/main.tf:222-228`

The token service accepts a shared `NODE_API_KEY`; `x-node-id` only has to match the request body and is not bound to a particular node credential. `node_id` has no pattern restriction and is used in the blob path.

Impact: any node with the shared key can upload logs as any node, poisoning analysis and potentially causing another node's remediation path to trigger.

Suggested fix: use per-node credentials, mTLS, or signed JWTs bound to node identity. Validate `node_id` with a strict regex and rate-limit token issuance.

### 20. Secrets and sensitive operational data are broadly persisted or printed

References:

- `infrastructure/render-vm-env.sh:107-118`
- `infrastructure/render-vm-env.sh:120-176`
- `local_agent/src/local_agent/reporter.py:57-112`
- `schemas/src/schemas/analysis_result.py:29`
- `analysis_agent/src/analysis_agent/ai_client.py:188`
- `log_service/src/log_service/config.py:15`
- `log_service/src/log_service/uploader.py:78-87`

The render script prints Service Bus connection strings, function keys, node API keys, and Cosmos master keys to stdout and writes env files without setting restrictive permissions. The local agent persists full configs, prompts, model responses, command stdout/stderr, rebuild output, and raw AI responses. Failed log batches are spooled under `/tmp` with default permissions.

Impact: terminal logs, env files, Cosmos containers, and local spool directories can become secondary secret stores.

Suggested fix: set `umask 077` or write files with mode `0600`, avoid printing secrets by default, redact configs/output before persistence, truncate raw responses/stdout/stderr, and spool logs under a service-owned directory with `0700` permissions.

### 21. Persistence and observability are not failure-resilient

References:

- `local_agent/src/local_agent/runtime.py:87-95`
- `local_agent/src/local_agent/runtime.py:118-136`
- `local_agent/src/local_agent/runtime.py:486-489`
- `local_agent/src/local_agent/runtime.py:496-523`
- `analysis_agent/src/analysis_agent/main.py:25-38`
- `analysis_agent/src/analysis_agent/ai_client.py:189-192`

Observation publish failures are swallowed. Observation worker exceptions are swallowed. Cosmos persistence exceptions are not caught in `persist_pending()`, so `persist_worker()` can fail and terminate `asyncio.gather()`. Invalid AI responses raise an exception without publishing a structured failure event.

Impact: the POC can lose the exact records needed to explain failure, or the daemon can stop due to status persistence errors.

Suggested fix: add local persistence spooling, retry/backoff for Cosmos, explicit degraded status events, and structured `analysis_failed` records when model output cannot be validated.

### 22. Contracts do not enforce schema version or extra-field drift

References:

- `schemas/src/schemas/normalized_log.py:6-16`
- `schemas/src/schemas/analysis_result.py:15-30`
- `schemas/src/schemas/decision.py:7-19`

Schema models use `schema_version: str = "1.0"`, not a literal. Pydantic defaults allow extra fields unless configured otherwise.

Impact: producers and consumers can drift silently, especially around AI-generated payloads and simulator fakes.

Suggested fix: use `Literal["1.0"]` for current contracts and `ConfigDict(extra="forbid")` for service-to-service schemas. Add explicit version migration when needed.

### 23. Correlation and incident identity are weak

References:

- `schemas/src/schemas/normalized_log.py:6-16`
- `schemas/src/schemas/observation.py:9-17`
- `schemas/src/schemas/analysis_result.py:15-30`
- `schemas/src/schemas/decision.py:7-19`
- `decision_agent/src/decision_agent/decision_engine.py:80-90`

There is no first-class `correlation_id` or `incident_id` spanning log/observation, analysis, decision, execution result, Git commit, and repair trace. `Decision.analysis_id` is populated from `analysis_result.original_message_id`, not a distinct analysis result ID.

Impact: demo visibility requires manual joins across unrelated IDs and timestamps.

Suggested fix: introduce `correlation_id`, `causation_id`, `analysis_id`, `source_message_id`, and `incident_id` fields. Generate once at ingress and propagate unchanged.

### 24. Simulator misses the riskiest runtime behavior

References:

- `simulator/src/simulator/fakes.py:35-59`
- `simulator/src/simulator/pipeline.py:94-173`

The simulator exercises happy-path handoffs but does not model Service Bus locks, completion, duplicate delivery, DLQ, push failure after switch, real rebuild targeting, Cosmos write failures, or local LLM timeout.

Impact: the supported validation command can pass while the real deployment fails in the decision-consumption and repair lifecycle.

Suggested fix: add fake Service Bus subscriptions with locks/delivery count/completion semantics, plus scenarios for duplicate decision redelivery, rebuild test failure, switch failure, push failure, and persistence failure.

## Suggested Fix Priority

1. Fix the core repair target: make `nixos-rebuild` operate on the exact edited repo config or flake target.
2. Disable or remove direct shell command execution from Service Bus decisions.
3. Make `apply_config` first-class in `decision_agent` and prefer it as the only mutating POC action.
4. Validate AI-derived fields, especially `affected_unit`, and never interpolate them into shell strings.
5. Rework Service Bus decision consumption so receive and complete happen on the same receiver.
6. Enforce idempotency in `local_agent` before any rebuild or command execution.
7. Collect real post-repair node state and persist it instead of synthetic success state.
8. Replace shared `local-agent` subscription with per-node filtered subscriptions.
9. Make repair lifecycle transactional around Git, rebuild, switch, push, and trace persistence.
10. Fix VM env rendering: include `NODE_ID`, align `OLLAMA_MODEL`, and stop printing secrets by default.
11. Add local durable state for safety limits and processed decisions.
12. Redact/truncate sensitive configs, prompts, raw AI output, stdout, stderr, and spooled logs.
13. Harden contracts with literal schema versions, extra-field forbidding, and stable correlation IDs.
14. Expand simulator realism around Service Bus and repair failure modes.

## Questions And Assumptions Needing Clarification

1. Is `/var/lib/phoe-nix-config-repo/configuration.nix` intended to be symlinked or otherwise wired to `/etc/nixos/configuration.nix` on the VM? If yes, that setup should be explicit in scripts/docs and verified before rebuild.
2. Should the POC still support legacy direct commands (`rollback`, `rebuild`, `restart_service`), or should all mutating decisions become `apply_config` context for the local repair loop?
3. Is the target config repo a traditional NixOS config or a flake? The correct rebuild command depends on this.
4. Is the POC expected to support more than one VM concurrently? If yes, a shared unfiltered `local-agent` subscription is a functional bug, not just a future hardening item.
5. Should config snapshots include full file contents, or would diffs plus redacted summaries be enough for demo visibility?
6. Are repair commits supposed to happen before or after `nixos-rebuild switch`? The answer determines how to handle push failure without losing traceability.
7. Which Ollama model tag is expected on the VM host: `gemma3:4b`, `gemma4:e4b`, or something operator-provided?
8. Should `NODE_API_KEY` identify a node or only authenticate any POC node? Per-node identity is needed to prevent cross-node log spoofing.

## Notes On POC Tradeoffs

The following appear intentional for a proof of concept, but should remain clearly labeled as non-production behavior:

- Shared `NODE_API_KEY` for token issuance.
- Public Azure endpoints and broad Key Vault access for easier setup.
- Local LLM-driven config editing.
- Full repair traces and command output for visibility.
- Limited approval/workflow controls.

However, the issues listed under Critical Issues are not merely production hardening gaps. They can make the POC demonstrate a repair that was not actually validated, execute unintended commands, or report health that was never observed.
