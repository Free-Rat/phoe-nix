# Phoe-nix Plan

> Last updated: 2026-06-11
> Scope: current repository state plus the next proof-of-concept work. Planned items stay labeled as planned.

## 1. Current repository state

Phoe-nix already has an end-to-end backend pipeline and a local simulator. The node-side Git-backed repair loop is already implemented; the remaining work is to harden it, improve visibility, and make it the primary POC path.

### Implemented today
- `token_service`: issues short-lived, path-scoped blob upload SAS URLs
- `log_service`: tails systemd journal, batches entries, retries uploads, and spools failed batches locally
- `log_router`: normalizes uploaded log batches and publishes messages to Service Bus
- `analysis_agent`: consumes normalized logs or observations, calls OpenCode, and emits `AnalysisResult`
- `decision_agent`: turns analysis into `Decision`, stores audit records in Cosmos DB, and publishes remediation intent
- `local_agent`: observation/state/executor/reporter logic, daemon runtime, and Git-backed repair loop already exist
- `schemas`: shared Pydantic message models
- `simulator`: best repo-level substitute for a real deployment

### What the simulator covers
- log-to-decision happy path
- observation-to-decision happy path
- local-agent decision consumption and repair-loop execution
- token failure spooling
- upload retry/recovery
- malformed log blob handling
- invalid AI response handling

## 2. Intended proof-of-concept direction

The next step is to make the already implemented repair loop the primary agentic repair loop on disposable VMs.

### Target loop
1. a node observes a problem
2. logs or observations enter the cloud pipeline
3. cloud services produce diagnosis and remediation context
4. `local_agent` receives the decision plus related analysis context
5. the local agent uses a host-side Ollama model to reason about the current node config
6. the local agent edits the shared config repo if needed
7. the local agent runs `nixos-rebuild test`
8. if test fails, the local agent uses the failure output for correction loops
9. if test succeeds, the local agent runs `nixos-rebuild switch`
10. the local agent commits and pushes the successful config change
11. the local agent reports the outcome and retries until healthy or out of attempts

### Target pipeline shape
- `analysis-input`: normalized logs and local observations
- `analysis-results`: analysis output from `analysis_agent`
- `final-decisions`: remediation decisions for `local_agent`

### Target local-agent responsibilities
- maintain a persistent clone of `https://github.com/Free-Rat/phoe-nix-config`
- pull before acting on a new decision
- make config changes to `configuration.nix`
- run `nixos-rebuild test` before `switch`
- keep repair traces, command output, and before/after config state
- handle push retries and merge conflicts
- report final node state

## 3. Near-term work remaining

### `local_agent`
- harden the already-implemented daemon/runtime and service wiring
- polish packaging and installation/update steps for the node-side runtime
- keep the config-repo pull/edit/test/switch/push loop stable under retries
- handle merge conflicts and retry limits

### Visibility
- add a minimal dashboard/TUI for:
  - node state
  - latest observations
  - analysis output
  - decisions
  - repair attempts and outcomes

### CI/CD
- add checked-in GitHub Actions for:
  - Python lint/test
  - simulator or repo-level validation
  - Terraform validation per module
- add deploy workflow(s) that reuse `scripts/deploy-functions.sh`

### Repo hygiene
- keep docs aligned when the implementation changes
- avoid duplicating the same status across multiple planning sections

## 4. Known gaps and risks
- live-Azure verification is still more manual than the simulator path
- AI response normalization is defensive, not exhaustive
- the current POC is intentionally not optimized for production safety or multi-node coordination
- CI is not yet checked in

## 5. Stable decisions
- POC nodes are disposable VMs
- cloud-side AI uses the OpenCode Go API
- node-side repair uses Ollama on the VM host
- the shared config repo is `https://github.com/Free-Rat/phoe-nix-config`
- the main editable file is `configuration.nix`
- `NODE_API_KEY` is required for the POC
- OpenCode requests need a non-default `User-Agent`

## 6. Helpful commands

```bash
cd infrastructure && nix develop
bash scripts/test.sh
bash scripts/simulate-deployment.sh
bash scripts/deploy-functions.sh <resource-group> <environment> token router analysis decision
bash infrastructure/smoke-test-poc.sh --node-id nixos
```
