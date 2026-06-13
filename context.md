# Code Context

## Files Retrieved
1. `docs/malenia-deployment.md` (lines 1-80, 120-180) - host-side runbook; shows the intended malenia workflow, VM env install, and local_agent/Ollama expectations.
2. `infrastructure/commands.md` (lines 1-120) - concrete SSH/scp commands for reaching the VM on `localhost:2222` and restarting `log_service` / `local_agent`.
3. `scripts/run-mock-simulation.sh` (lines 1-80) - the only repo script that actually launches a fresh VM via `./run-vm.sh` and waits for port 2222.
4. `scripts/check-deployment.sh` (lines 599-614, 618-632, 760-762) - verifies the VM by checking the qemu process name and that `localhost:2222` is reachable.
5. `scripts/run-live-azure-vm-e2e.py` (lines 31-35, 392-442, 536-647, 718-800) - live POC checker that assumes SSH on port 2222, validates `local_agent` health, and warns about common VM caveats.
6. `scripts/run-live-azure-vm-e2e.sh` (lines 1-20) - wrapper that runs the Python live check in the repo environment.
7. `docs/testing-plan.md` (lines 1-30) - documents `scripts/run-live-azure-vm-e2e.sh` as the full live VM repair path.
8. `docs/implementation.md` (lines 1-45) - explains current `local_agent` behavior and the repair loop it is supposed to run on the VM.

## Key Code

- VM start / wait logic is in `scripts/run-mock-simulation.sh`:
  ```bash
  if ! remote_ssh "ss -ltn '( sport = :2222 )' | grep -q 2222"; then
    remote_ssh "cd '$REMOTE_CONFIG_DIR' && rm -f result && setsid -f bash -lc 'exec env PHOE_NIX_SOURCE_ROOT='\''$REMOTE_CODE_DIR'\'' ./run-vm.sh > /tmp/phoe-nix-vm-mock-sim.log 2>&1 < /dev/null'"
  fi
  wait_for_guest
  ```
  This is the clearest scripted way in the repo to boot a disposable VM on a host named `malenia`.

- The guest connection details in `infrastructure/commands.md`:
  ```bash
  scp -P 2222 /tmp/phoe-nix-vm-env/log-service.env user@localhost:/tmp/log-service.env
  scp -P 2222 /tmp/phoe-nix-vm-env/local-agent.env user@localhost:/tmp/local-agent.env
  ssh -p 2222 user@localhost '... systemctl restart log_service local_agent ...'
  ```

- The live checker assumes the VM is already up on `user@localhost:2222` and that `local_agent` is active:
  ```python
  DEFAULT_VM_SSH_TARGET = "user@localhost"
  DEFAULT_VM_SSH_PORT = 2222
  ...
  if ssh_command(args, f"systemctl is-active {shlex.quote(args.vm_service_name)}", timeout=10) != "active":
      raise VerificationError(f"{args.vm_service_name} is not active on the VM")
  ```

- `scripts/check-deployment.sh` validates the VM the same way operators do:
  ```bash
  pid="$(pgrep -f "qemu-kvm.*-name nixos" ... )"
  timeout 2 bash -c "exec 3<>/dev/tcp/${host}/${port}"
  ```

## Architecture

- The repo does **not** expose a standalone `start-vm` command in the phoe-nix tree.
- The documented path is:
  1. start the disposable NixOS VM on malenia via the `run-vm.sh` path referenced by `scripts/run-mock-simulation.sh`
  2. wait for SSH on `localhost:2222`
  3. copy `log-service.env` and `local-agent.env` into the guest
  4. restart `log_service` and `local_agent`
  5. verify Ollama reachability from inside the guest (`http://10.0.2.2:11434/api/tags`)
- `scripts/run-live-azure-vm-e2e.sh` / `.py` are for the full Azure→VM repair loop, not VM bootstrapping, but they are useful once the VM is already reachable.

## Start Here

Open `scripts/run-mock-simulation.sh` first. It is the only repo script that shows the VM boot sequence and the port-2222 readiness check in one place.

## Actionable steps

1. On `malenia`, run the mock-simulation bootstrap path that launches `./run-vm.sh` from the config checkout.
2. Wait until `ss -ltn '( sport = :2222 )'` reports the guest port is listening.
3. Copy the rendered env files into the VM:
   - `scp -P 2222 /tmp/phoe-nix-vm-env/log-service.env user@localhost:/tmp/log-service.env`
   - `scp -P 2222 /tmp/phoe-nix-vm-env/local-agent.env user@localhost:/tmp/local-agent.env`
4. Install them as root and restart services:
   - `ssh -p 2222 user@localhost 'sudo install ... && sudo systemctl restart log_service local_agent'`
5. Confirm the guest can reach host Ollama:
   - `ssh -p 2222 user@localhost 'curl http://10.0.2.2:11434/api/tags'`

## Caveats

- `scripts/run-mock-simulation.sh` is a mock-Azure helper, not the production deploy path.
- The repo’s live checker expects the VM to already be up; it does not itself boot QEMU.
- `scripts/check-deployment.sh` specifically looks for a `qemu-kvm` process named `nixos` and for `localhost:2222` reachability.
- `local_agent` can be blocked by env mismatches; the live checker warns if `REBUILD_SWITCH_COMMAND` is still `nixos-rebuild switch`, if `COOLDOWN_SECONDS` is nonzero, or if `MAX_REMEDIATIONS_PER_HOUR` is too low.
