# Proof Of Concept Direction

## Purpose

This document describes the intended proof-of-concept direction for Phoe-nix.

The goal is not production readiness. The goal is to demonstrate an agentic self-healing NixOS pipeline on disposable virtual machines.

In this proof of concept:

- nodes are expected to be disposable
- arbitrary and unsafe config changes are acceptable
- the system is allowed to experiment, fail, observe, and try again
- the main value is demonstrating the closed loop from observation to repair

## Core Idea

Phoe-nix should not stop at detecting incidents and emitting commands.

The intended direction is a repeated repair loop:

1. a node observes a problem
2. logs or observations enter the cloud pipeline
3. the cloud produces diagnosis and remediation context
4. the local agent receives that context
5. the local agent uses an LLM locally to interpret the problem in the context of the node's current configuration
6. the local agent updates the shared node configuration repository if needed
7. the local agent runs `nixos-rebuild test`, uses the failure output for correction loops if necessary, and then runs `nixos-rebuild switch`
8. the local agent reports what changed and what happened
9. the cycle repeats until the node is healthy or retries are exhausted

This makes the `local_agent` the real autonomous repair participant in the system.

## Why This Direction

The earlier design centered on structured actions like restart, rollback, or rebuild.

That approach is safer, but it does not match the core proof-of-concept goal: config-level automated repair.

For the proof of concept, we prefer:

- simpler architecture
- stronger demo value
- fewer up-front safety abstractions
- more freedom for the local agent to act autonomously

## Example Scenario

Example: one node is trying to connect to another node, but `sshd` is not running on the target.

1. the node trying to connect emits errors into the journal
2. `log_service` collects those journal entries and uploads them to Blob Storage
3. `log_router` normalizes the log entries and publishes them to `analysis-input`
4. `analysis_agent` reads the evidence and produces analysis text saying that the target node likely needs `services.openssh.enable = true`
5. `decision_agent` produces a remediation decision for the affected node
6. `local_agent` receives both the decision and the related analysis context
7. the local agent pulls the latest `https://github.com/Free-Rat/phoe-nix-config` state if needed and reads `configuration.nix`
8. the local agent uses its local LLM to inspect current NixOS configuration and decide how to change it
9. the local agent edits `configuration.nix` so that SSH is enabled
10. the local agent runs `nixos-rebuild test`
11. if the test fails, the local agent feeds the error back into the local LLM and retries
12. if the test succeeds, the local agent runs `nixos-rebuild switch`
13. the local agent commits and pushes the successful config change back to the shared config repository
14. if push fails because of a merge conflict, the local agent pulls, tries to resolve the conflict, reruns `nixos-rebuild test`, and then pushes again
15. future observations confirm whether the issue is resolved

The important detail is that the cloud does not need to prescribe the exact config patch in a strict schema for the proof of concept. The local node is allowed to interpret the problem and attempt the repair itself.

## Pipeline Shape

The intended Service Bus topic split is:

- `analysis-input`: normalized logs and local observations
- `analysis-results`: analysis output from `analysis_agent`
- `final-decisions`: remediation decisions for `local_agent`

The local agent should receive decision context rich enough to understand the problem, not just a bare shell command.

In practice that means the decision flow should include:

- a `Decision`
- a link to the corresponding `AnalysisResult`
- enough human-readable analysis text for the local LLM to use directly

## Intended Roles

### `analysis_agent`

- consume logs and observations from `analysis-input`
- call OpenCode Go API
- produce diagnosis text and any lightweight metadata that helps later stages
- publish to `analysis-results`

For the proof of concept, analysis may be mostly raw text rather than strictly structured remediation categories.

### `decision_agent`

- consume analysis output from `analysis-results`
- decide which node should act
- produce remediation intent for that node
- publish to `final-decisions`

For the proof of concept, the decision may stay relatively loose. It does not need to fully describe a safe structured config patch.

### `local_agent`

- observe local node state continuously
- publish local observations to `analysis-input`
- consume decisions from `final-decisions`
- read the related analysis context
- use a local LLM running on the host's Ollama service to reason about the node's current configuration
- keep a local clone of `https://github.com/Free-Rat/phoe-nix-config`
- pull that repo before each new decision and also on a periodic refresh cadence so nodes converge on shared repairs
- make arbitrary config changes to `configuration.nix` if needed
- run `nixos-rebuild test` first and use failures as an additional repair loop before `switch`
- push successful changes back to the shared config repository
- report what happened

This is the key agentic step in the proof of concept.

## What The Local Agent Should Report

Because the local agent is allowed to make arbitrary changes, visibility matters more than safety.

The local agent should persist and report at least:

- the triggering observation or log correlation id
- the received analysis text
- the received decision text
- the original config text or a relevant before snapshot
- the edited config text or a diff
- the git revision before and after the repair attempt
- commands that were run
- stdout/stderr from rebuild attempts
- retry count
- final node state after the attempt

## Local LLM Access

For the proof of concept, the local LLM should not be exposed publicly to Azure.

- cloud-side services continue to use the OpenCode Go API
- on-node repair uses Ollama running on the host machine
- the VM accesses Ollama directly over HTTP on the host/VM private network

For VM-based proof-of-concept setups, the preferred approach is direct private HTTP rather than an SSH tunnel or reverse proxy because it is the smallest working setup.

## Shared Config Repository

For the proof of concept, node repairs operate on a shared public repository:

- repository: `https://github.com/Free-Rat/phoe-nix-config`
- primary editable file: `configuration.nix`

The intended local-agent workflow is:

1. keep a local checkout of the config repository on the node
2. pull before each new decision
3. also refresh every 5 minutes because another node may already have fixed the same issue
4. read current `configuration.nix`
5. ask the local LLM for an updated full-file configuration
6. write the candidate configuration
7. run `nixos-rebuild test`
8. if the test fails, feed the failure back into the LLM and retry up to the configured limit
9. if the test succeeds, run `nixos-rebuild switch`
10. commit and push the successful change
11. if push fails due to remote changes, pull, attempt to resolve the conflict, rerun `nixos-rebuild test`, and push again

## What We Are Explicitly Not Optimizing For Yet

- production safety
- strict command whitelisting
- strongly bounded config mutation schemas
- approval workflows
- rollback orchestration across many nodes
- guaranteed deterministic repairs

Those may matter later, but they are not the proof-of-concept target.

## Minimal Guardrails Still Worth Keeping

Even as a proof of concept, a few limits still help:

- cap retries so the node does not loop forever
- always save the before/after config state
- always report rebuild outputs and failure reasons
- keep topic-level and document-level correlation ids so the pipeline is visible in the TUI

## Relationship To Existing Code

The current repository implementation is still closer to a structured cloud-analysis plus command-execution model.

The intended next direction changes that emphasis:

- `analysis_agent` becomes more text-forward
- `decision_agent` becomes more intent-forward
- `local_agent` becomes the primary repair engine
- the frontend/TUI focuses on making the full pipeline visible during demos

## Summary

The proof of concept aims to show that:

1. nodes can publish evidence about failure
2. cloud-side agents can analyze and route that evidence
3. a local agent can use that context to repair NixOS configuration
4. the system can observe the outcome and continue the loop

This is the direction the remaining implementation should follow.
