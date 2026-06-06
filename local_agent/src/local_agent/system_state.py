from __future__ import annotations

import json
import subprocess
from pathlib import Path

from schemas import NodeState


def run_command(command: list[str], *, timeout_seconds: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout_seconds, check=False)


def collect_node_state(*, command_runner=run_command) -> NodeState:
    failed_units_result = command_runner(["systemctl", "--failed", "--plain", "--no-legend"])
    failed_units = []
    for line in failed_units_result.stdout.splitlines():
        unit = line.split()[0].strip() if line.split() else ""
        if unit:
            failed_units.append(unit)

    uptime_result = command_runner(["cat", "/proc/uptime"])
    uptime_seconds = None
    if uptime_result.returncode == 0 and uptime_result.stdout.strip():
        uptime_seconds = int(float(uptime_result.stdout.split()[0]))

    return NodeState(failed_units=failed_units, uptime_seconds=uptime_seconds)
