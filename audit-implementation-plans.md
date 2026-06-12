# Audit Implementation Plans

Date: 2026-06-12

This document expands every point from `audit.md` into an implementation plan. Each section was prepared as a separate review/planning pass and then consolidated.

## Priority Order

1. Fix rebuild targeting so `local_agent` validates the edited repo config.
2. Remove/gate direct shell command execution from Service Bus decisions.
3. Make `apply_config` first-class and default for mutating remediation.
4. Fix Service Bus receive/settlement and enforce idempotency before side effects.
5. Render consistent node identity and fix per-node final-decision routing.
6. Improve post-repair state reporting, transactional repair lifecycle, and simulator realism.
7. Harden credentials, persistence, schema drift, correlation IDs, and sensitive data handling.

## 1. Repair Loop Rebuilds Wrong Config

Root cause: `execute_repair_loop()` writes `repo_path/config_file_path`, but `LocalAgentConfig` defaults to `nixos-rebuild test` and `nixos-rebuild switch`, which normally read `/etc/nixos/configuration.nix`.

Files:

- `local_agent/src/local_agent/config.py`
- `local_agent/src/local_agent/git_repo.py`
- `local_agent/src/local_agent/repair_planner.py`
- `local_agent/src/local_agent/reporter.py`
- `local_agent/tests/test_config.py`
- `local_agent/tests/test_git_repo.py`
- `local_agent/tests/test_repair_planner.py`

Implementation:

1. Add `resolve_config_path(repo_path, config_file_path)` in `git_repo.py`.
2. Reject absolute `CONFIG_FILE_PATH` and `..` traversal.
3. Generate default rebuild commands dynamically from `CONFIG_REPO_PATH` and `CONFIG_FILE_PATH`.
4. Use `nixos-rebuild test -I nixos-config=<absolute repo config>` and matching `switch`, unless explicit env overrides are supplied.
5. If the target repo is a flake, add `REBUILD_TARGET_TYPE=flake` and generate `nixos-rebuild test --flake <repo>#<host>` instead.
6. Record the resolved config path/rebuild target in `RepairAttempt` and repair trace documents.
7. Keep explicit custom rebuild commands supported for experiments.

Tests:

- Dynamic command defaults include the resolved repo config path.
- Explicit `REBUILD_TEST_COMMAND` and `REBUILD_SWITCH_COMMAND` still override.
- Paths with spaces are shell-quoted while command execution still uses strings.
- Invalid config paths are rejected.
- Repair traces include the exact rebuild target.

Verification:

```bash
bash scripts/test.sh
bash scripts/simulate-deployment.sh
```

Open questions:

- Is `phoe-nix-config` traditional NixOS config or a flake?
- Is `/var/lib/phoe-nix-config-repo/configuration.nix` ever symlinked to `/etc/nixos/configuration.nix` on the VM?

## 2. Remote Command Execution Through Service Bus Decisions

Root cause: `Decision.command` is externally supplied, and `local_agent.executor.run_subprocess()` executes it with `shell=True`.

Files:

- `schemas/src/schemas/decision.py`
- `decision_agent/src/decision_agent/decision_engine.py`
- `local_agent/src/local_agent/config.py`
- `local_agent/src/local_agent/runtime.py`
- `local_agent/src/local_agent/executor.py`
- `local_agent/tests/test_executor.py`
- `local_agent/tests/test_runtime.py`
- `decision_agent/tests/test_decision_engine.py`

Implementation:

1. Add a closed action model for supported actions.
2. Add structured `parameters: dict[str, str]` to `Decision`.
3. Stop producing executable shell strings in `decision_agent`; set `command=""` by default.
4. In `local_agent`, reject non-empty `Decision.command` unless an explicit unsafe config flag is set.
5. Replace command-string execution with argv execution for any retained direct actions.
6. Use `subprocess.run(argv, shell=False, ...)`.
7. Store a display command generated with `shlex.quote()` for audit only.
8. Keep legacy direct commands disabled by default in rendered VM env.

Tests:

- A malicious `Decision.command` is rejected by default.
- No command runner is called for rejected legacy command payloads.
- Retained direct actions derive argv from structured parameters.
- `run_subprocess()` calls `subprocess.run(..., shell=False)`.

Verification:

```bash
bash scripts/test.sh
```

Open questions:

- Should legacy direct commands remain at all, or should they be deleted?

## 3. Prompt Injection To Command Injection Through `affected_unit`

Root cause: AI-generated `affected_unit` can be interpolated into `systemctl restart ...`, then executed through a shell.

Files:

- `schemas/src/schemas/analysis_result.py`
- `analysis_agent/src/analysis_agent/ai_client.py`
- `analysis_agent/src/analysis_agent/prompt_builder.py`
- `decision_agent/src/decision_agent/decision_engine.py`
- `local_agent/src/local_agent/executor.py`

Implementation:

1. Add shared systemd unit validation, ideally in `schemas`.
2. Allow only service units matching `^[A-Za-z0-9_.@:-]+\.service$` for restart actions.
3. Prefer trusted fallback unit metadata from logs over model-provided units.
4. If no trusted fallback exists, accept model unit only after validation.
5. Move restart execution to structured parameters: `parameters={"unit":"nginx.service"}`.
6. Execute restart via `['systemctl', 'restart', unit]`.
7. Update prompts to tell the model `affected_unit` must be one `.service` unit or `null`.

Tests:

- Valid units pass: `nginx.service`, `getty@tty1.service`, `systemd-journald.service`.
- Shell metacharacters, whitespace, paths, substitutions, and non-service units are rejected.
- Trusted fallback unit overrides malicious model unit.

## 4. `apply_config` Can Fail Before Reaching `local_agent`

Root cause: `normalize_suggested_action()` recognizes `apply_config`, but `build_command()` has no explicit `apply_config` branch and can raise if no Nix assignment regex matches.

Files:

- `decision_agent/src/decision_agent/decision_engine.py`
- `decision_agent/tests/test_decision_engine.py`
- `decision_agent/tests/test_app.py`
- `simulator/tests/test_pipeline.py`

Implementation:

1. Add `if suggested_action == "apply_config": return ""` in `build_command()`.
2. Keep the Nix-assignment heuristic only as fallback inference, not as a requirement.
3. Add alias tests for `apply config`, `apply-config`, and `applyconfig`.
4. Add app-level test proving prose-only `apply_config` writes/publishes a command-free decision.

Verification:

```bash
bash scripts/test.sh
```

## 5. Service Bus Receive And Complete Use Different Receivers

Root cause: `receive_messages()` closes the receiver before returning messages; `complete_message()` opens a new receiver to settle the old locked message.

Files:

- `local_agent/src/local_agent/bus_client.py`
- `local_agent/src/local_agent/runtime.py`
- `local_agent/tests/test_bus_client.py`
- `local_agent/tests/test_runtime.py`

Implementation:

1. Replace split receive/complete helpers with receiver-scoped processing.
2. Keep a receiver context open across receive, handle, and complete.
3. Use `asyncio.to_thread()` only for blocking SDK calls while keeping receiver ownership in `decision_worker()`.
4. Settle on the same receiver instance that received the message.
5. On handler failure, do not complete; optionally abandon or dead-letter based on failure type.
6. Add `complete_failed` status on settlement failure.
7. Consider auto-lock renewal for long repair loops.

Tests:

- Success completes on the same fake receiver that returned the message.
- Handler error does not complete.
- Completion failure records status.
- Regression fake fails if completion uses a different receiver.

## 6. Idempotency Key Is Generated But Never Enforced

Root cause: `Decision.idempotency_key` exists, but `local_agent` never claims or records it before side effects.

Files:

- `local_agent/src/local_agent/config.py`
- `local_agent/src/local_agent/runtime.py`
- `local_agent/src/local_agent/persistence.py`
- `infrastructure/02-cosmos/main.tf`
- `local_agent/tests/test_runtime.py`
- `local_agent/tests/test_persistence.py`

Implementation:

1. Add Cosmos container `idempotency-keys`, partitioned by `/node_id`.
2. Add `COSMOSDB_IDEMPOTENCY_CONTAINER_NAME` config.
3. Add `claim_idempotency_key()` using Cosmos conditional `create_item()`.
4. Treat conflict as duplicate and skip execution.
5. Claim before `execute_decision()` or `execute_repair_loop()`.
6. Mark records `completed` or `failed` after execution.
7. Fail closed if idempotency cannot be claimed and Cosmos/idempotency is required.
8. For local/test mode, support an in-memory or file-backed idempotency store.

Tests:

- Duplicate `apply_config` decision executes once.
- Wrong-node and `no_action` do not claim keys.
- Claim happens before side effects.
- Failed repair marks the key failed.

## 7. Successful Repairs Report Synthetic Healthy State

Root cause: repair success creates `NodeState(failed_units=[])` instead of collecting post-repair state.

Files:

- `local_agent/src/local_agent/runtime.py`
- `local_agent/src/local_agent/executor.py`
- `local_agent/src/local_agent/reporter.py`
- `schemas/src/schemas/node_state.py`
- `local_agent/tests/test_runtime.py`
- `local_agent/tests/test_executor.py`

Implementation:

1. Collect live node state after every direct command and config repair.
2. Use `runtime.dependencies.read_node_state()` after repair completion, regardless of success/failure.
3. If post-state collection fails, preserve previous known state and mark post-state as `unknown` or record a service-status error.
4. Update summaries so unknown state does not read as healthy.
5. Persist actual post-state to `node-state-current`.

Tests:

- Successful repair reports remaining failed unit if collector reports it.
- Healthy is reported only when collector confirms no failed units.
- Collector failure produces explicit unknown/degraded state.

## 8. Shared Unfiltered `final-decisions` Subscription

Root cause: Terraform creates one `local-agent` subscription; every VM uses it; wrong-node decisions are skipped and can be completed by the wrong consumer.

Files:

- `infrastructure/04-stateless/main.tf`
- `infrastructure/04-stateless/variables.tf`
- `infrastructure/render-vm-env.sh`
- `decision_agent/src/decision_agent/main.py`
- `local_agent/src/local_agent/config.py`
- `local_agent/src/local_agent/runtime.py`

Implementation:

1. Publish `node_id` as a Service Bus application property on final decisions.
2. Create one subscription per node, e.g. `local-agent-nixos`.
3. Add SQL filter per subscription: `node_id = '<node-id>'`.
4. Ensure default broad subscription rule is removed/replaced.
5. Render node-specific `SERVICEBUS_SUBSCRIPTION_LOCAL_AGENT`.
6. Default local-agent subscription to `local-agent-${NODE_ID}`.
7. Do not complete wrong-node messages if a shared subscription is ever used.

Tests:

- `publish_decision()` sets `application_properties['node_id']`.
- `NODE_ID=node-01` defaults subscription to `local-agent-node-01`.
- Wrong-node message is not completed.

## 9. Repair Loop Can Leave Repo/Remote/System Inconsistent

Root cause: candidates are written directly to the working tree; failed candidates remain; switch can happen before push succeeds; push failure triggers destructive refresh while system may be switched.

Files:

- `local_agent/src/local_agent/repair_planner.py`
- `local_agent/src/local_agent/git_repo.py`
- `local_agent/src/local_agent/reporter.py`
- `local_agent/src/local_agent/runtime.py`

Implementation:

1. Split `commit_and_push()` into smaller Git primitives.
2. Add explicit repair statuses: `failed_test`, `failed_evidence_push`, `failed_switch`, `applied`, `applied_remote_pending`.
3. Restore failed test candidates before the next attempt; pass the failed candidate/error in prompt context instead of using dirty repo state.
4. Commit candidate on a unique repair branch after test succeeds.
5. Push the repair branch before `switch` as remote evidence.
6. Run `switch` only after candidate evidence is preserved.
7. Push/update target branch after successful switch.
8. If final target push fails, do not hard reset; return partial state requiring operator action.
9. Persist candidate revision, evidence branch, active system generation before/after, and remote sync status.

Tests:

- Evidence push failure prevents switch.
- Switch failure preserves candidate evidence.
- Target push failure returns `applied_remote_pending` and does not reset evidence.

## 10. Safety State Is In-Memory And Hourly Cap Never Resets

Root cause: runtime always starts from `new_state()`, and `remediations_this_hour` only increments.

Files:

- `local_agent/src/local_agent/state.py`
- `local_agent/src/local_agent/runtime.py`
- `local_agent/src/local_agent/persistence.py`
- `local_agent/src/local_agent/config.py`

Implementation:

1. Replace `remediations_this_hour` counter with `remediation_timestamps`.
2. Count only timestamps in a sliding one-hour window.
3. Add local durable state file, default `/var/lib/phoe-nix/local-agent-state.json`.
4. Load state in `LocalAgentRuntime.__post_init__()`.
5. Save state immediately after remediation and observation hash updates.
6. Do not restore `ongoing_remediation=True` blindly after crash.
7. Use atomic writes and restrictive permissions for local state.

Tests:

- Cap blocks inside a sliding hour and allows after expiration.
- State round-trips to disk.
- Restart preserves recent remediation timestamps.

## 11. Rendered VM Env Omits Required `NODE_ID`

Root cause: `log_service.load_config()` requires `NODE_ID`, but `render-vm-env.sh` does not write it. `local_agent` also defaults to `localhost` when rendered env omits it.

Files:

- `infrastructure/render-vm-env.sh`
- `log_service/tests/test_config.py`
- `local_agent/tests/test_config.py`
- docs under `infrastructure/` and `docs/`

Implementation:

1. Add `NODE_ID=${NODE_ID:-nixos}` to renderer defaults.
2. Add `--node-id NODE_ID` argument.
3. Validate non-empty and safe node ID.
4. Render `NODE_ID` into `log-service.env` and `local-agent.env`.
5. Use the same ID for token requests, observations, and decision targeting.

Validation:

```bash
NODE_ID=nixos bash infrastructure/render-vm-env.sh --write /tmp/phoe-nix-env --cosmos off
```

## 12. Rendered Ollama Model Conflicts With Code Default

Root cause: code defaults to `gemma3:4b`; renderer hardcodes `gemma4:e4b`.

Files:

- `infrastructure/render-vm-env.sh`
- `local_agent/src/local_agent/config.py`
- `local_agent/tests/test_config.py`
- `scripts/phase5-verify.sh`

Implementation:

1. Define `DEFAULT_OLLAMA_MODEL = "gemma3:4b"` in local-agent config.
2. In renderer, set `OLLAMA_MODEL=${OLLAMA_MODEL:-gemma3:4b}`.
3. Render the env override value, not a hardcoded mismatched tag.
4. Update docs to describe override.
5. Improve `phase5-verify.sh` to confirm configured model appears in `/api/tags`.

## 13. Hardware Config Can Be Used Locally But Never Committed

Root cause: `sync_local_hardware_configuration()` copies `hardware-configuration.nix`, but `commit_and_push()` stages only `configuration.nix`.

Files:

- `local_agent/src/local_agent/repair_planner.py`
- `local_agent/src/local_agent/git_repo.py`
- `local_agent/tests/test_repair_planner.py`
- `local_agent/tests/test_git_repo.py`

Implementation:

1. Make `sync_local_hardware_configuration()` return support file paths to stage.
2. Pass those paths to `commit_and_push()`.
3. Stage `git add -- configuration.nix hardware-configuration.nix`.
4. Validate all staged paths are repo-relative and not traversal.
5. Consider making support files configurable later.

Open question: `hardware-configuration.nix` is node-specific. For multi-node, prefer node-local hardware imports or host-specific flake outputs.

## 14. AI Can Override Trusted Envelope Fields

Root cause: `parse_analysis_response()` uses `payload.setdefault()` for trusted fields, letting model-provided values win.

Files:

- `analysis_agent/src/analysis_agent/ai_client.py`
- `analysis_agent/tests/test_ai_client.py`
- `analysis_agent/tests/test_message_handler.py`

Implementation:

1. Assign trusted fields unconditionally: `schema_version`, `node_id`, `original_message_id`, `source_type`, `timestamp`, `raw_ai_response`.
2. For `affected_unit`, force trusted fallback when present.
3. Allow model-provided `affected_unit` only when no fallback exists and it passes validation.
4. Build a clean payload from selected model fields instead of validating the raw model object.

Tests:

- Malicious model `node_id`, `source_type`, `original_message_id`, and timestamp are overwritten.
- Trusted fallback unit wins over model unit.

## 15. One Malformed Journal Entry Drops Whole Blob

Root cause: `normalize_blob()` uses a list comprehension and aborts on any entry exception.

Files:

- `log_router/src/log_router/normalizer.py`
- `log_router/src/log_router/main.py`
- `log_router/tests/test_normalizer.py`
- `simulator/src/simulator/pipeline.py`

Implementation:

1. Split envelope parsing from entry parsing.
2. Keep invalid JSON/missing envelope fatal.
3. Iterate entries individually and collect valid records plus `LogNormalizationError` records.
4. Preserve original entry index in Service Bus message ID.
5. Log or persist structured errors without publishing them to `analysis-input`.
6. Update simulator malformed blob scenario to expect partial success plus errors.

Tests:

- Mixed valid/invalid blob publishes valid entries and records errors.
- Bad timestamp, bad priority, non-object entry each drops only that entry.
- Envelope errors still fail.

## 16. Blob Lifecycle Prefix Alignment

Root cause: `logs/` prefix is probably correct for Azure lifecycle semantics, but the invariant is implicit and hardcoded.

Files:

- `infrastructure/03-blob-storage/main.tf`
- `infrastructure/03-blob-storage/variables.tf`
- `infrastructure/04-stateless/main.tf`
- `token_service/tests/test_sas_generator.py`

Implementation:

1. Derive lifecycle prefix from the container resource: `"${azurerm_storage_container.logs.name}/"`.
2. Optionally add `logs_container_name` variable shared by storage/stateless modules.
3. Add Terraform comments explaining account-relative lifecycle prefix semantics.
4. Keep SAS `blob_name` container-relative and returned `blob_path` container-qualified.

Validation:

```bash
cd infrastructure/03-blob-storage && terraform fmt -check && terraform validate
```

## 17. Direct Commands Contradict Local-Agent-Centered Repair Model

Root cause: POC docs say cloud provides context and local-agent repairs config, but `decision_agent` still emits privileged commands and `local_agent` prioritizes `decision.command`.

Files:

- `analysis_agent/src/analysis_agent/prompt_builder.py`
- `decision_agent/src/decision_agent/decision_engine.py`
- `decision_agent/src/decision_agent/config.py`
- `local_agent/src/local_agent/config.py`
- `local_agent/src/local_agent/runtime.py`

Implementation:

1. Make `apply_config` the default mutating decision.
2. Convert `restart_service`, `rollback`, and `rebuild` suggestions to `apply_config` in default POC mode.
3. Preserve original suggested action and affected unit in `remediation_text`.
4. Update prompts to recommend only `apply_config` or `no_action`.
5. Add explicit `ENABLE_LEGACY_DIRECT_COMMANDS=false` flags if old behavior must remain.
6. Local agent should reject non-empty `command` by default.

## 18. Service Bus Authorization Is Overprivileged

Root cause: one namespace-level Shared Access Policy has send and listen and is reused by all functions and local agents.

Files:

- `infrastructure/04-stateless/main.tf`
- `infrastructure/render-vm-env.sh`
- Service config/main files for `log_router`, `analysis_agent`, `decision_agent`, and `local_agent`
- `scripts/run-live-ollama-pipeline.py`
- `scripts/publish-test-decision.sh`

Implementation:

1. Use separate managed identities per Function App.
2. Scope RBAC by topic/subscription: router send only to `analysis-input`, analysis receive/send only where needed, decision receive/send only where needed.
3. Use identity-based Service Bus bindings for Functions.
4. Split local-agent Service Bus credentials into observation send and decision receive.
5. Stop rendering namespace-wide Service Bus connection strings to VMs.
6. Rotate old keys after migration.
7. Update scripts to require explicit admin/test credentials instead of auto-fetching `SharedAccessPolicy`.

Open question: if subscription-scoped SAS is not possible for local agents, consider per-node queues or managed identity.

## 19. Node Auth And SAS Issuance Use Shared Key/Self-Asserted Identity

Root cause: `NODE_API_KEY` is global; `x-node-id` only has to match request body; `node_id` is used in blob path without strict validation.

Files:

- `token_service/src/token_service/auth.py`
- `token_service/src/token_service/models.py`
- `token_service/src/token_service/config.py`
- `token_service/src/token_service/app.py`
- `log_service/src/log_service/config.py`
- `log_service/src/log_service/token_client.py`
- `infrastructure/04-stateless/main.tf`
- `infrastructure/render-vm-env.sh`

Implementation:

1. Add strict node ID regex, e.g. `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`.
2. Replace shared key with per-node credential map in Key Vault.
3. Token service looks up credential by node ID and uses `hmac.compare_digest()`.
4. Unknown node, mismatched node, or wrong key returns generic 401.
5. Render only the current node's key to VM env.
6. Add rate limiting via APIM or durable per-node counter.
7. Optionally validate blob path node ID matches payload node ID in `log_router`.

## 20. Secrets And Sensitive Data Are Broadly Persisted/Printed

Root cause: env renderer prints secrets; local-agent persists full configs/prompts/stdout/stderr; raw AI responses are stored; logs spool under `/tmp` with default permissions.

Files:

- `infrastructure/render-vm-env.sh`
- `scripts/run-live-ollama-pipeline.py`
- `scripts/mock_azure.py`
- `scripts/publish-test-decision.sh`
- `local_agent/src/local_agent/reporter.py`
- `local_agent/src/local_agent/runtime.py`
- `local_agent/src/local_agent/executor.py`
- `analysis_agent/src/analysis_agent/ai_client.py`
- `log_service/src/log_service/config.py`
- `log_service/src/log_service/uploader.py`
- `log_service/src/log_service/main.py`

Implementation:

1. Add redaction/truncation helpers for SAS URLs, connection strings, function keys, API keys, bearer tokens, passwords, and common secret names.
2. Redact before truncating; store hash/length/truncated metadata.
3. `render-vm-env.sh` should set `umask 077`, write `0600` files, and not print secrets unless `--print-secrets` is explicit.
4. Store redacted previews of configs, prompts, model responses, stdout, stderr, and push messages.
5. Add a final persistence scrubber in `persist_pending()`.
6. Change log spool default to `/var/lib/phoe-nix/log-service/spool`, directory `0700`, files `0600`.
7. Stop printing raw journal messages by default.
8. Make debug scripts safe-by-default with explicit unsafe raw output flags.

## 21. Persistence And Observability Are Not Failure-Resilient

Root cause: publish errors are swallowed; Cosmos failures can kill the daemon; no durable local spool; invalid AI responses produce no structured failure output.

Files:

- `local_agent/src/local_agent/runtime.py`
- `local_agent/src/local_agent/persistence.py`
- `local_agent/src/local_agent/config.py`
- `analysis_agent/src/analysis_agent/main.py`
- `analysis_agent/src/analysis_agent/message_handler.py`
- `analysis_agent/src/analysis_agent/ai_client.py`

Implementation:

1. Add local persist spool: `pending/`, optional `dead-letter/`, atomic JSON writes.
2. Write to spool before enqueueing in-memory persistence.
3. Retry Cosmos writes with backoff; do not drop requests before success.
4. Load due spooled records on startup.
5. Observation publish failure should enqueue `publish_failed`, not `published`.
6. `observe_worker` and `persist_worker` should catch/report and continue.
7. Publish valid `analysis_failed` records for invalid AI outputs.
8. Distinguish validation failures from transient infrastructure failures for Azure retry semantics.

## 22. Contracts Do Not Enforce Schema Version Or Extra Fields

Root cause: schemas use `schema_version: str = "1.0"`; Pydantic ignores extra fields by default.

Files:

- all files under `schemas/src/schemas/*.py`
- producers/consumers in all services
- `analysis_agent/src/analysis_agent/ai_client.py`

Implementation:

1. Add shared strict base with `ConfigDict(extra="forbid")`.
2. Use `schema_version: Literal["1.0"]`; decide whether it is required or defaulted.
3. Apply strictness to nested models such as `NodeState` and `AnalysisContext`.
4. Producers should set schema version explicitly using a shared constant.
5. Filter/construct clean AI payloads before validating `AnalysisResult`.
6. Add tests that bad schema versions and unknown fields are rejected.

Open question: requiring `schema_version` will break any queued/fixture payloads that omitted it.

## 23. Correlation And Incident Identity Are Weak

Root cause: messages lack stable domain IDs across log/observation, analysis, decision, execution, traces, and commits.

Files:

- shared schemas
- `log_service/src/log_service/models.py`
- `log_service/src/log_service/storage.py`
- `log_router/src/log_router/normalizer.py`
- `analysis_agent/src/analysis_agent/message_handler.py`
- `analysis_agent/src/analysis_agent/ai_client.py`
- `decision_agent/src/decision_agent/decision_engine.py`
- `local_agent/src/local_agent/monitor.py`
- `local_agent/src/local_agent/reporter.py`
- `local_agent/src/local_agent/runtime.py`

Implementation:

1. Add `correlation_id`, `causation_id`, `source_message_id`, and `incident_id` to contracts.
2. Add `event_id` for `NormalizedLog`, `observation_id` for `Observation`, `analysis_id` for `AnalysisResult`.
3. Generate correlation at ingress and propagate unchanged.
4. Set `Decision.analysis_id = AnalysisResult.analysis_id`, not original message ID.
5. Use correlation/incident IDs in Service Bus properties, Cosmos docs, service status, repair traces, execution results, and Git commit messages.
6. Update idempotency key to consider `incident_id` if desired.

Tests:

- End-to-end simulator assertion that one `correlation_id` appears in normalized log, analysis, decision, execution result, repair trace, and status docs.

## 24. Simulator Misses Riskiest Runtime Behavior

Root cause: fake Service Bus is append-only and no-lock; local-agent path bypasses `decision_worker`; repair/Cosmos/LLM fakes mostly succeed.

Files:

- `simulator/src/simulator/fakes.py`
- `simulator/src/simulator/pipeline.py`
- `simulator/src/simulator/fixtures.py`
- `simulator/src/simulator/cli.py`
- `simulator/tests/test_pipeline.py`

Implementation:

1. Add subscription-aware `FakeServiceBus` with receive locks, receiver identity, delivery count, completion, abandon, lock expiry, and DLQ.
2. Make `process_local_agent()` exercise `decision_worker()` instead of iterating topic history.
3. Add fake command runner with configurable rebuild test/switch results.
4. Add fake repo push failure and revision tracking.
5. Add Cosmos write failure injection.
6. Add LLM timeout/error injection.
7. Add scenario outputs for active messages, DLQ, delivery counts, completed IDs, command history, repo revision, push failures, and Cosmos failures.

Tests:

- Completion with wrong receiver fails and redelivers.
- Duplicate decision redelivery is observable.
- Rebuild test failure does not switch/push.
- Switch failure records failed repair.
- Push failure after switch is visible.
- Cosmos failure and LLM timeout are surfaced.
