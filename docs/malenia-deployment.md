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
- The **blob -> log_router -> analysis-input** live path is still failing:
  uploading a real log batch via `token_service` produced a blob, but no
  normalized message arrived on a debug `analysis-input` subscription within
  180 seconds.
- The **analysis-results -> decision_agent -> final-decisions** live path is
  also still failing at the trigger layer: publishing a valid
  `AnalysisResult` to `analysis-results` left `analysis-results/decision-agent`
  with `activeMessageCount = 1`, and no matching message arrived on a debug
  `final-decisions` subscription.

Interpretation so far:

- The HTTP function (`token_service`) is healthy.
- The locally run analysis code + Ollama path is healthy.
- The Azure-triggered workers (`log_router` blob trigger and `decision_agent`
  Service Bus trigger) are registered but not currently firing in this dev
  environment.

## Do not assume

- Do not assume the VM is running just because Azure resources exist.
- Do not assume cloud functions are using Ollama; `analysis_agent` is still OpenCode-backed.
- Do not treat this file as a live resource inventory.
