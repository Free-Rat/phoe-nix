# Proof Of Concept Direction

## Purpose

This document describes the intended proof-of-concept direction for Phoe-nix: an agentic NixOS repair loop on disposable VMs. It is not a production plan.

## Direction

The proof of concept should move repair responsibility into `local_agent`.

The intended flow is:

1. a node observes a problem
2. logs and observations flow through the existing cloud pipeline
3. cloud analysis and decisions provide context, not a fully prescribed patch
4. `local_agent` uses a local LLM on the VM host to interpret the issue against the current configuration
5. it edits `configuration.nix` in `https://github.com/Free-Rat/phoe-nix-config`
6. it runs `nixos-rebuild test`, retries on failure, then runs `nixos-rebuild switch`
7. it reports the change, rebuild output, and final node state

## Design goals

- optimize for demo value and iteration speed
- keep the local agent autonomous enough to repair configuration itself
- retain enough reporting to understand what happened
- cap retries so the node does not loop forever

## Current repo state

The repository already has structured cloud-side services, shared schemas, a simulator, and an implemented `local_agent` daemon/runtime with the Git-backed repair loop. The POC direction is to shift the center of repair work into `local_agent` and make that loop the default path on disposable VMs.

So this document is about the next step, not a description of everything that already exists.

## Pipeline shape

Preferred topic flow:

- `analysis-input`
- `analysis-results`
- `final-decisions`

`analysis_agent` should stay text-forward, `decision_agent` intent-forward, and `local_agent` should become the primary repair engine.

## Minimum visibility

The local agent should persist:

- the triggering observation or correlation id
- the analysis and decision text
- the before/after config or diff
- the git revision before and after
- the commands that were run
- the rebuild output
- the retry count
- the final node state

## Not the goal yet

- production safety
- strict command whitelisting
- deterministic repairs
- multi-node rollback orchestration
- approval workflows

Those may matter later, but they are not the proof-of-concept target.
