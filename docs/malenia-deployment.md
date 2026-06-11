# malenia — deployment notes

This is a host-side runbook for the malenia workstation. It is meant to help deploy and verify the current phoe-nix POC, not to serve as a live inventory snapshot.

## What the current repo expects

- Cloud pipeline: `token_service` → `log_service` → `log_router` → `analysis_agent` → `decision_agent`.
- Shared contracts: `schemas`.
- Local validation: `simulator`.
- Cloud AI: `analysis_agent` still calls OpenCode at `https://opencode.ai/zen/go/v1/chat/completions` with `deepseek-v4-flash` and reads `OpenCodeApiKey` from Key Vault.
- Node-side repair: `local_agent` uses Ollama on the VM host (`http://10.0.2.2:11434`) and targets `https://github.com/Free-Rat/phoe-nix-config`.
- Preferred topic flow: `analysis-input` → `analysis-results` → `final-decisions`.

## Deployment / verification flow

1. Put `TF_VAR_opencode_api_key` and `TF_VAR_node_api_key` in the repo-root `.env`.
2. Enter the Azure shell:

   ```bash
   cd infrastructure
   nix develop
   az login
   az account set --subscription <subscription-id>
   ```

3. Apply Terraform with `./apply.sh`.
4. From the repo root, deploy the functions:

   ```bash
   bash scripts/deploy-functions.sh "$RG" "$ENV" token router analysis decision
   ```

5. Render the VM env files:

   ```bash
   bash infrastructure/render-vm-env.sh --write /tmp/phoe-nix-vm-env
   ```

6. Copy `log-service.env` and `local-agent.env` into the VM, then restart `log_service` and `local_agent`.
7. Run the live Azure smoke check:

   ```bash
   bash infrastructure/smoke-test-poc.sh --node-id nixos
   ```

8. Use the live deployment checker when you want the fuller inventory:

   ```bash
   bash scripts/check-deployment.sh
   ```

## Operational notes

- Run `scripts/check-deployment.sh` from the infrastructure Nix shell so `az` is on PATH.
- `scripts/check-deployment.sh` reports cloud resources, Service Bus topology, Cosmos containers, Azure Function apps, VM reachability, and rendered VM env files. Use `--quick` to skip slow checks and `--json` for machine-readable output.
- `scripts/deploy-functions.sh` stages zip artifacts under `.build/functions/`.
- `scripts/phase5-verify.sh` is the quickest way to confirm the local-agent decision path; it exits once `service-status` shows `decision/received`.
- Keep an eye on the Ollama model setting: `local_agent` defaults to `OLLAMA_MODEL=gemma3:4b`, while `infrastructure/render-vm-env.sh` still writes `OLLAMA_MODEL=gemma4:e4b`.

## 2026-06-11 live test findings

Follow-up work on 2026-06-11 deployed the current local code to all four
Function Apps with:

```bash
cd infrastructure
nix develop -c bash -lc 'cd .. && ./scripts/deploy-functions.sh "$RG" "$ENV" token router analysis decision'
```

Observed afterwards:

- The Azure Functions admin API sees all four functions registered:
  `token_service`, `log_router`, `analysis_agent`, `decision_agent`.
- `token_service` is definitely live: calling
  `https://func-project-healer-dev-token.azurewebsites.net/api/token?code=...`
  with the node headers returns a valid SAS payload.
- A helper script now exists at `scripts/run-live-ollama-pipeline.py`. It can
  stop the deployed `analysis_agent` app briefly, publish a test message, run
  `analysis_agent.analyze_message()` locally against Ollama `gemma4:12b`,
  publish the resulting `AnalysisResult` back to Azure, and wait for
  `decision_agent` to emit a `Decision`.
- The local Ollama analysis leg **works**. On the `cowsay` test case it
  returned a valid `AnalysisResult` with remediation hint
  `environment.systemPackages = [ "cowsay" ];`.
- The first failure mode was deployment-side: after `config-zip` deploys, the
  non-HTTP triggers were not active until `syncfunctiontriggers` was called for
  the Function Apps. Evidence:
  - `token_service` worked immediately because it is HTTP-triggered.
  - `decision_agent` code worked when invoked directly through the admin API.
  - after running `POST .../syncfunctiontriggers` for `func-...-router` and
    `func-...-decision`, both trigger paths started working.
- The **analysis-results -> decision_agent -> final-decisions** failure was
  therefore a **trigger-sync problem**, not a bad payload or broken
  `decision_agent` implementation.
- The **blob -> log_router -> analysis-input** failure was partly the same
  trigger-sync problem, but there was also a test-timeout issue: once the
  trigger was synced, a blob uploaded through `token_service` was eventually
  normalized, but it appeared more than 180 seconds later. The earlier test was
  therefore a false negative caused by blob-trigger latency on the Y1
  consumption plan.
- Direct blob uploads to the same storage account/container were observed to
  normalize successfully after sync, and the delayed `token_service` upload was
  later seen on `analysis-input` as well.

Interpretation so far:

- The HTTP function (`token_service`) is healthy.
- The locally run analysis code + Ollama path is healthy.
- `decision_agent` application code is healthy.
- The main deployment bug was missing trigger sync after function deployment.
- Blob-trigger tests from `token_service` need a longer timeout than 180s in
  this environment.
- `scripts/run-live-ollama-pipeline.py` was updated to:
  - wait up to 600s by default for the blob-trigger hop
  - match the specific test message on `analysis-results`
  - match the resulting `Decision` by `analysis_id` on `final-decisions`
  so background `local_agent` traffic does not create false positives.
- A full rerun after these fixes succeeded on the blob entry path:
  `blob -> log_router -> local Ollama analysis -> analysis-results -> decision_agent -> final-decisions`.
  The final decision for test node `malenia-ollama-test10` was:
  - `action = rebuild`
  - `remediation_text = environment.systemPackages = [ "cowsay" ];`

## 2026-06-11 VM full-pipeline repair replay

A later 2026-06-11 run validated the real cloud path for a disposable VM on
`malenia` and then replayed the resulting real decision into the VM
`local_agent` with `OLLAMA_MODEL=gpt-oss:20b`.

Real cloud path exercised:

- VM-side `log_service`
- blob upload
- Azure `log_router`
- Azure `analysis_agent` using OpenCode / `deepseek-v4-flash`
- Azure `decision_agent`
- `final-decisions`

Test case used:

- transient unit: `bootstrap-banner12.service`
- emitted message:

  ```text
  ERROR: cowsay command not found; fix configuration.nix by adding environment.systemPackages = [ pkgs.cowsay ];
  ```

Observed real cloud outputs:

- normalized message id:
  `logs/nixos/fd7864d4-5ec6-48e0-adda-c6f643c8bd9f:3`
- `analysis_agent.suggested_action = apply_config`
- `analysis_agent.remediation_hint = Add \`environment.systemPackages = [ pkgs.cowsay ];\` to configuration.nix and rebuild.`
- `decision_agent.decision_id = cb485014-b071-4afb-ba13-716fa66bb744`
- `decision_agent.action = apply_config`

VM/local-agent-side fixes needed during this replay:

- use `OLLAMA_MODEL=gpt-oss:20b`
- relax runtime safety limits in the VM override env:
  - `COOLDOWN_SECONDS=0`
  - `MAX_REMEDIATIONS_PER_HOUR=100`
- replace the VM `switch` step with a second `nixos-rebuild test` because
  `nixos-rebuild switch` in this VM still tried to touch `/boot`
- mark both repos as safe for Git on the VM:
  - `/home/user/phoe-nix-config-origin.git`
  - `/var/lib/phoe-nix-config-repo`

Final observed result:

- `local_agent` successfully applied the config repair
- Cosmos `service-status` recorded `repair/completed`
- Cosmos `execution-results.success = true`
- the guest-local origin repo advanced to commit
  `1d0944b7abe2d6604a49fbe89b3fc1b5a1f09cdb`
- `configuration.nix` in that repo now includes `cowsay`
- `cowsay` became available in the running VM (`/run/current-system/sw/bin/cowsay`)

Caveat:

- while replaying the same `decision_id`, duplicate retained messages on the
  filtered debug subscription caused some earlier `decision/blocked` status
  records before the successful replay. The successful repair entries are the
  later `repair/completed` records for the same decision id.

## Do not assume

- Do not assume the VM is running just because Azure resources exist.
- Do not assume cloud functions are using Ollama; `analysis_agent` is still OpenCode-backed.
- Do not treat this file as a live resource inventory.
