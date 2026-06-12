#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import request

try:
    from azure.cosmos import CosmosClient  # type: ignore[import-not-found]
    from azure.servicebus import ServiceBusClient  # type: ignore[import-not-found]
    from azure.storage.blob import BlobClient  # type: ignore[import-not-found]
except ImportError as error:  # pragma: no cover - operator-facing import guard
    raise SystemExit(
        "Missing Azure Python dependencies. Run from the repo environment "
        "(for example via nix develop / uv where azure-servicebus, azure-storage-blob, and azure-cosmos are installed)."
    ) from error


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PROJECT_NAME = "project-healer"
DEFAULT_ENV = "dev"
DEFAULT_NODE_ID = "nixos"
DEFAULT_VM_SSH_TARGET = "user@localhost"
DEFAULT_VM_SSH_PORT = 2222
DEFAULT_VM_ENV_PATH = "/etc/phoe-nix/local-agent.env"
DEFAULT_VM_SERVICE_NAME = "local_agent"
DEFAULT_COSMOS_DB = "project-healer"
DEFAULT_REPAIR_TIMEOUT_SECONDS = 900.0
DEFAULT_BLOB_TIMEOUT_SECONDS = 600.0
DEFAULT_TOPIC_TIMEOUT_SECONDS = 180.0
DEFAULT_PACKAGE_CANDIDATES = ("sl", "figlet", "toilet", "cowsay")


@dataclass(frozen=True)
class VmState:
    hostname: str
    repo_path: str
    config_file_path: str
    ollama_base_url: str
    ollama_model: str
    rebuild_switch_command: str
    cooldown_seconds: str
    max_remediations_per_hour: str
    before_revision: str
    before_config_text: str


@dataclass(frozen=True)
class CosmosHandles:
    service_status: Any
    execution_results: Any
    repair_traces: Any


class VerificationError(RuntimeError):
    pass


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key, value)


def run_command(command: list[str], *, timeout: float | None = None, capture_output: bool = True) -> str:
    result = subprocess.run(
        command,
        check=True,
        text=True,
        capture_output=capture_output,
        timeout=timeout,
    )
    return result.stdout.strip() if capture_output else ""


def run_az(*args: str, timeout: float | None = None, capture_output: bool = True) -> str:
    return run_command(["az", *args], timeout=timeout, capture_output=capture_output)


def resolve_tenant_suffix() -> str:
    explicit = os.environ.get("AZURE_TENANT_SUFFIX")
    if explicit:
        return explicit
    tenant_id = run_az("account", "show", "--query", "tenantId", "-o", "tsv", timeout=30)
    if not tenant_id:
        raise VerificationError("Azure CLI returned an empty tenantId")
    suffix = tenant_id.replace("-", "")[:6]
    os.environ["AZURE_TENANT_SUFFIX"] = suffix
    return suffix


def resolve_sb_namespace(project_name: str, env_name: str, explicit: str | None) -> str:
    if explicit:
        return explicit
    return f"sb-{project_name}-{env_name}-{resolve_tenant_suffix()}"


def resolve_servicebus_connection(resource_group: str, namespace: str) -> str:
    if os.environ.get("SERVICEBUS_CONNECTION"):
        return os.environ["SERVICEBUS_CONNECTION"]
    auth_rule = "SharedAccessPolicy"
    key = run_az(
        "servicebus",
        "namespace",
        "authorization-rule",
        "keys",
        "list",
        "--resource-group",
        resource_group,
        "--namespace-name",
        namespace,
        "--name",
        auth_rule,
        "--query",
        "primaryKey",
        "-o",
        "tsv",
        timeout=30,
    )
    if not key:
        raise VerificationError("Unable to resolve Service Bus shared access key")
    connection = (
        f"Endpoint=sb://{namespace}.servicebus.windows.net/;"
        f"SharedAccessKeyName={auth_rule};SharedAccessKey={key}"
    )
    os.environ["SERVICEBUS_CONNECTION"] = connection
    return connection


def resolve_cosmos_handles(resource_group: str, cosmos_account: str, database_name: str) -> CosmosHandles:
    key = run_az(
        "cosmosdb",
        "keys",
        "list",
        "--resource-group",
        resource_group,
        "--name",
        cosmos_account,
        "--type",
        "keys",
        "--query",
        "primaryMasterKey",
        "-o",
        "tsv",
        timeout=30,
    )
    if not key:
        raise VerificationError("Unable to resolve Cosmos DB primary key")
    endpoint = f"https://{cosmos_account}.documents.azure.com:443/"
    client = CosmosClient(endpoint, key)
    database = client.get_database_client(database_name)
    return CosmosHandles(
        service_status=database.get_container_client("service-status"),
        execution_results=database.get_container_client("execution-results"),
        repair_traces=database.get_container_client("repair-traces"),
    )


def resolve_token_function_key(resource_group: str, token_app: str) -> str:
    try:
        return run_az(
            "functionapp",
            "function",
            "keys",
            "list",
            "--resource-group",
            resource_group,
            "--name",
            token_app,
            "--function-name",
            "token_service",
            "--query",
            "default",
            "-o",
            "tsv",
            timeout=30,
        )
    except subprocess.CalledProcessError:
        return run_az(
            "functionapp",
            "keys",
            "list",
            "--resource-group",
            resource_group,
            "--name",
            token_app,
            "--query",
            "functionKeys.default",
            "-o",
            "tsv",
            timeout=30,
        )


def call_token_service(*, token_url: str, node_id: str, node_api_key: str, timeout_seconds: float) -> dict[str, Any]:
    payload = json.dumps({"node_id": node_id}).encode("utf-8")
    req = request.Request(
        token_url,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "X-Node-ID": node_id,
            "X-API-Key": node_api_key,
        },
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return json.loads(response.read().decode("utf-8"))


def upload_blob_via_sas(*, sas_url: str, payload: bytes, timeout_seconds: float) -> None:
    blob_client = BlobClient.from_blob_url(sas_url)
    blob_client.upload_blob(payload, overwrite=True, timeout=timeout_seconds)


def build_log_payload(*, node_id: str, entries: list[dict[str, Any]]) -> bytes:
    payload = {
        "node_id": node_id,
        "entries": entries,
        "uploaded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    return json.dumps(payload).encode("utf-8")


def create_subscription(resource_group: str, namespace: str, topic_name: str, subscription_name: str) -> None:
    run_az(
        "servicebus",
        "topic",
        "subscription",
        "create",
        "--resource-group",
        resource_group,
        "--namespace-name",
        namespace,
        "--topic-name",
        topic_name,
        "--name",
        subscription_name,
        timeout=30,
        capture_output=False,
    )


def delete_subscription(resource_group: str, namespace: str, topic_name: str, subscription_name: str) -> None:
    try:
        run_az(
            "servicebus",
            "topic",
            "subscription",
            "delete",
            "--resource-group",
            resource_group,
            "--namespace-name",
            namespace,
            "--topic-name",
            topic_name,
            "--name",
            subscription_name,
            timeout=30,
            capture_output=False,
        )
    except subprocess.CalledProcessError:
        pass


def message_body_bytes(message: Any) -> bytes:
    body = getattr(message, "body", b"")
    if isinstance(body, bytes):
        return body
    if isinstance(body, bytearray):
        return bytes(body)
    if isinstance(body, str):
        return body.encode("utf-8")
    parts: list[bytes] = []
    for part in body:
        if isinstance(part, bytes):
            parts.append(part)
        elif isinstance(part, bytearray):
            parts.append(bytes(part))
        else:
            parts.append(bytes(part))
    return b"".join(parts)


def receive_matching_message(
    *,
    servicebus_connection: str,
    topic_name: str,
    subscription_name: str,
    timeout_seconds: float,
    matcher,
    wanted_description: str,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    with ServiceBusClient.from_connection_string(servicebus_connection) as client:
        receiver = client.get_subscription_receiver(topic_name=topic_name, subscription_name=subscription_name)
        with receiver:
            while time.monotonic() < deadline:
                batch = receiver.receive_messages(max_message_count=10, max_wait_time=5)
                for message in batch:
                    if matcher(message):
                        receiver.complete_message(message)
                        return message
                    receiver.abandon_message(message)
    raise TimeoutError(f"Timed out waiting for {wanted_description} on {topic_name}/{subscription_name}")


def receive_specific_message(
    *,
    servicebus_connection: str,
    topic_name: str,
    subscription_name: str,
    wanted_message_id: str,
    timeout_seconds: float,
) -> Any:
    return receive_matching_message(
        servicebus_connection=servicebus_connection,
        topic_name=topic_name,
        subscription_name=subscription_name,
        timeout_seconds=timeout_seconds,
        matcher=lambda message: getattr(message, "message_id", None) == wanted_message_id,
        wanted_description=f"message_id={wanted_message_id}",
    )


def ssh_command(
    args: argparse.Namespace, remote_script: str, *, timeout: float = 30.0, use_sudo: bool = False
) -> str:
    final_script = remote_script
    if use_sudo:
        sudo_command = f"sudo -n bash -lc {shlex.quote(remote_script)}"
        if args.vm_sudo_password:
            escaped_password = shlex.quote(args.vm_sudo_password)
            sudo_with_password = f"printf '%s\\n' {escaped_password} | sudo -S bash -lc {shlex.quote(remote_script)}"
            final_script = f"{sudo_command} || {sudo_with_password}"
        else:
            final_script = sudo_command
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ConnectTimeout=8",
        "-p",
        str(args.vm_ssh_port),
        args.vm_ssh_target,
        f"bash -lc {shlex.quote(final_script)}",
    ]
    return run_command(command, timeout=timeout)


def fetch_vm_env(args: argparse.Namespace) -> dict[str, str]:
    keys = [
        "SERVICEBUS_ENABLED",
        "SERVICEBUS_CONNECTION",
        "CONFIG_REPO_PATH",
        "CONFIG_FILE_PATH",
        "OLLAMA_BASE_URL",
        "OLLAMA_MODEL",
        "REBUILD_SWITCH_COMMAND",
        "COOLDOWN_SECONDS",
        "MAX_REMEDIATIONS_PER_HOUR",
    ]
    key_pattern = "|".join(keys)
    remote_script = (
        "for path in "
        f"{shlex.quote(args.vm_env_path + '.defaults')} {shlex.quote(args.vm_env_path)}; do "
        'if [ -f "$path" ]; then '
        f"grep -E '^({key_pattern})=' \"$path\" || true; "
        "fi; "
        "done"
    )
    values: dict[str, str] = {}
    for line in ssh_command(args, remote_script, use_sudo=True).splitlines():
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return {key: values.get(key, "") for key in keys}


def fetch_vm_state(args: argparse.Namespace) -> VmState:
    print("== VM preflight ==")
    ssh_command(args, "true", timeout=10)
    print(f"PASS: SSH reachable at {args.vm_ssh_target}:{args.vm_ssh_port}")

    if ssh_command(args, f"systemctl is-active {shlex.quote(args.vm_service_name)}", timeout=10) != "active":
        raise VerificationError(f"{args.vm_service_name} is not active on the VM")
    print(f"PASS: {args.vm_service_name} is active")

    env = fetch_vm_env(args)
    if env.get("SERVICEBUS_ENABLED") != "1":
        raise VerificationError(f"SERVICEBUS_ENABLED is {env.get('SERVICEBUS_ENABLED')!r}, expected '1'")
    if not env.get("SERVICEBUS_CONNECTION", "").strip():
        raise VerificationError("SERVICEBUS_CONNECTION is empty in the VM env")
    print("PASS: local_agent Service Bus receive path is configured")

    repo_path = env.get("CONFIG_REPO_PATH") or "/var/lib/phoe-nix-config-repo"
    config_file_path = env.get("CONFIG_FILE_PATH") or "configuration.nix"
    config_abs_path = f"{repo_path.rstrip('/')}/{config_file_path.lstrip('/')}"
    ssh_command(args, f"test -f {shlex.quote(config_abs_path)}", timeout=10, use_sudo=True)
    print(f"PASS: config file exists at {config_abs_path}")

    ollama_base_url = env.get("OLLAMA_BASE_URL") or "http://10.0.2.2:11434"
    ollama_model = env.get("OLLAMA_MODEL") or ""
    if args.expected_ollama_model and ollama_model != args.expected_ollama_model:
        raise VerificationError(
            f"VM OLLAMA_MODEL is {ollama_model!r}, expected {args.expected_ollama_model!r}"
        )
    tags_output = ssh_command(
        args,
        f"curl -sS --max-time 10 {shlex.quote(ollama_base_url.rstrip('/'))}/api/tags",
        timeout=20,
    )
    tags_payload = json.loads(tags_output)
    available_models = {item.get('name', '') for item in tags_payload.get('models', []) if isinstance(item, dict)}
    if args.expected_ollama_model and not any(
        model == args.expected_ollama_model or model.startswith(f"{args.expected_ollama_model}:")
        for model in available_models
    ):
        raise VerificationError(
            f"Expected Ollama model {args.expected_ollama_model!r} not found on VM; available: {sorted(available_models)}"
        )
    print(f"PASS: Ollama reachable at {ollama_base_url} and model {args.expected_ollama_model} is available")

    before_revision = ssh_command(args, f"git -C {shlex.quote(repo_path)} rev-parse HEAD", timeout=15, use_sudo=True)
    before_config_text = ssh_command(args, f"cat {shlex.quote(config_abs_path)}", timeout=15, use_sudo=True)
    hostname = ssh_command(args, "hostname", timeout=10)

    rebuild_switch_command = env.get("REBUILD_SWITCH_COMMAND", "")
    if rebuild_switch_command == "nixos-rebuild switch":
        print("WARN: REBUILD_SWITCH_COMMAND is still 'nixos-rebuild switch'; on some POC VMs that can fail when /boot is not writable")
    if env.get("COOLDOWN_SECONDS") not in {"", "0"}:
        print(f"WARN: COOLDOWN_SECONDS={env.get('COOLDOWN_SECONDS')}; recent repairs may block this run")
    if env.get("MAX_REMEDIATIONS_PER_HOUR"):
        try:
            if int(env["MAX_REMEDIATIONS_PER_HOUR"]) < 100:
                print(
                    f"WARN: MAX_REMEDIATIONS_PER_HOUR={env['MAX_REMEDIATIONS_PER_HOUR']}; safety limits may block this run"
                )
        except ValueError:
            print(
                f"WARN: MAX_REMEDIATIONS_PER_HOUR={env['MAX_REMEDIATIONS_PER_HOUR']!r} is not an integer; safety checks may misbehave"
            )

    return VmState(
        hostname=hostname,
        repo_path=repo_path,
        config_file_path=config_file_path,
        ollama_base_url=ollama_base_url,
        ollama_model=ollama_model,
        rebuild_switch_command=rebuild_switch_command,
        cooldown_seconds=env.get("COOLDOWN_SECONDS", ""),
        max_remediations_per_hour=env.get("MAX_REMEDIATIONS_PER_HOUR", ""),
        before_revision=before_revision,
        before_config_text=before_config_text,
    )


def remote_command_exists(args: argparse.Namespace, binary_name: str) -> bool:
    output = ssh_command(
        args,
        f"if command -v {shlex.quote(binary_name)} >/dev/null 2>&1; then echo yes; else echo no; fi",
        timeout=10,
    )
    return output.strip() == "yes"


def select_test_package(args: argparse.Namespace, vm_state: VmState) -> str:
    if args.package:
        package = args.package
        if package in vm_state.before_config_text:
            raise VerificationError(
                f"Requested package {package!r} already appears in {vm_state.config_file_path}; pick another --package"
            )
        if remote_command_exists(args, package):
            raise VerificationError(
                f"Requested package {package!r} already appears to be installed on the VM; pick another --package"
            )
        return package

    for package in args.package_candidates:
        if package in vm_state.before_config_text:
            continue
        if remote_command_exists(args, package):
            continue
        return package
    raise VerificationError(
        "Could not find a safe package candidate that is absent from both the repo config and the current VM PATH"
    )


def build_fake_log_entry(*, package: str, hostname: str, run_id: str) -> dict[str, Any]:
    now_us = int(time.time() * 1_000_000)
    return {
        "__REALTIME_TIMESTAMP": str(now_us),
        "MESSAGE": (
            f"bootstrap-banner.sh[421]: /etc/local/bin/bootstrap-banner.sh: line 12: {package}: command not found. "
            f"The node startup banner depends on {package} being installed system-wide. "
            f"Fix configuration.nix by adding environment.systemPackages = [ pkgs.{package} ]; "
            f"Correlation run id: {run_id}."
        ),
        "_SYSTEMD_UNIT": f"bootstrap-banner-e2e-{run_id}.service",
        "PRIORITY": "3",
        "_HOSTNAME": hostname,
        "SYSLOG_IDENTIFIER": "bootstrap-banner.sh",
    }


def run_smoke_test(node_id: str, node_api_key: str) -> None:
    print("== Azure smoke preflight ==")
    env = os.environ.copy()
    env["NODE_ID"] = node_id
    env["NODE_API_KEY"] = node_api_key
    subprocess.run(
        ["bash", str(ROOT_DIR / "infrastructure" / "smoke-test-poc.sh"), "--node-id", node_id],
        check=True,
        cwd=ROOT_DIR,
        env=env,
    )


def query_items(container: Any, query: str, parameters: list[dict[str, object]]) -> list[dict[str, Any]]:
    return list(container.query_items(query=query, parameters=parameters, enable_cross_partition_query=True))


def fetch_decision_cosmos_snapshot(
    *,
    cosmos: CosmosHandles,
    decision_id: str,
    node_id: str,
    since_epoch: int,
) -> dict[str, list[dict[str, Any]]]:
    return {
        "service_status": query_items(
            cosmos.service_status,
            (
                "SELECT TOP 25 c.id, c.node_id, c.stage, c.status, c.detail, c.timestamp, c.correlation_id, c._ts "
                "FROM c WHERE c.node_id=@node_id AND c.correlation_id=@decision_id AND c._ts >= @since "
                "ORDER BY c._ts DESC"
            ),
            [
                {"name": "@node_id", "value": node_id},
                {"name": "@decision_id", "value": decision_id},
                {"name": "@since", "value": since_epoch},
            ],
        ),
        "execution_results": query_items(
            cosmos.execution_results,
            (
                "SELECT TOP 5 c.execution_id, c.node_id, c.decision_id, c.success, c.completed_at, c.command, c.stdout, c.stderr "
                "FROM c WHERE c.node_id=@node_id AND c.decision_id=@decision_id AND c._ts >= @since "
                "ORDER BY c._ts DESC"
            ),
            [
                {"name": "@node_id", "value": node_id},
                {"name": "@decision_id", "value": decision_id},
                {"name": "@since", "value": since_epoch},
            ],
        ),
        "repair_traces": query_items(
            cosmos.repair_traces,
            (
                "SELECT TOP 10 c.id, c.node_id, c.decision_id, c.attempt_number, c.push_success, c.push_message, "
                "c.repo_revision_after, c.test_exit_code, c.switch_exit_code, c.test_stderr, c.switch_stderr "
                "FROM c WHERE c.node_id=@node_id AND c.decision_id=@decision_id AND c._ts >= @since "
                "ORDER BY c.attempt_number DESC"
            ),
            [
                {"name": "@node_id", "value": node_id},
                {"name": "@decision_id", "value": decision_id},
                {"name": "@since", "value": since_epoch},
            ],
        ),
    }


def print_decision_cosmos_snapshot(snapshot: dict[str, list[dict[str, Any]]], *, decision_id: str) -> None:
    print(f"== Cosmos records for decision {decision_id} ==")
    print(json.dumps(snapshot, indent=2))


def poll_local_agent(
    *,
    cosmos: CosmosHandles,
    decision_id: str,
    node_id: str,
    since_epoch: int,
    timeout_seconds: float,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, dict[str, Any] | None]:
    print("== Waiting for local_agent ==")
    deadline = time.monotonic() + timeout_seconds
    seen_status_ids: set[str] = set()
    printed_execution = False
    printed_trace = False
    all_statuses: list[dict[str, Any]] = []
    last_execution: dict[str, Any] | None = None
    last_trace: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        statuses = query_items(
            cosmos.service_status,
            (
                "SELECT TOP 25 c.id, c.stage, c.status, c.detail, c.timestamp, c._ts "
                "FROM c WHERE c.node_id=@node_id AND c.correlation_id=@decision_id AND c._ts >= @since "
                "ORDER BY c._ts DESC"
            ),
            [
                {"name": "@node_id", "value": node_id},
                {"name": "@decision_id", "value": decision_id},
                {"name": "@since", "value": since_epoch},
            ],
        )
        for item in reversed(statuses):
            status_id = str(item.get("id", ""))
            if not status_id or status_id in seen_status_ids:
                continue
            seen_status_ids.add(status_id)
            all_statuses.append(item)
            print(
                f"[{item.get('timestamp', '')}] local_agent {item.get('stage')}/{item.get('status')} "
                f"detail={item.get('detail', '')}"
            )

        executions = query_items(
            cosmos.execution_results,
            (
                "SELECT TOP 1 c.execution_id, c.decision_id, c.success, c.completed_at, c.command, c.stdout, c.stderr "
                "FROM c WHERE c.node_id=@node_id AND c.decision_id=@decision_id AND c._ts >= @since "
                "ORDER BY c._ts DESC"
            ),
            [
                {"name": "@node_id", "value": node_id},
                {"name": "@decision_id", "value": decision_id},
                {"name": "@since", "value": since_epoch},
            ],
        )
        if executions:
            last_execution = executions[0]
            if not printed_execution:
                printed_execution = True
                print(
                    f"[{last_execution.get('completed_at', '')}] execution-results success={last_execution.get('success')} "
                    f"command={last_execution.get('command', '')}"
                )

        traces = query_items(
            cosmos.repair_traces,
            (
                "SELECT TOP 1 c.id, c.decision_id, c.attempt_number, c.push_success, c.push_message, "
                "c.repo_revision_after, c.test_exit_code, c.switch_exit_code "
                "FROM c WHERE c.node_id=@node_id AND c.decision_id=@decision_id AND c._ts >= @since "
                "ORDER BY c.attempt_number DESC"
            ),
            [
                {"name": "@node_id", "value": node_id},
                {"name": "@decision_id", "value": decision_id},
                {"name": "@since", "value": since_epoch},
            ],
        )
        if traces:
            last_trace = traces[0]
            if not printed_trace:
                printed_trace = True
                print(
                    f"repair-trace attempt={last_trace.get('attempt_number')} push_success={last_trace.get('push_success')} "
                    f"repo_revision_after={last_trace.get('repo_revision_after', '')}"
                )

        success_seen = any(
            item.get("stage") == "repair" and item.get("status") == "completed" for item in all_statuses
        )
        repair_failed = any(
            item.get("stage") == "repair" and item.get("status") == "failed" for item in all_statuses
        )
        decision_failed = any(
            item.get("stage") == "decision" and item.get("status") in {"failed", "blocked"}
            for item in all_statuses
        )
        if success_seen and last_execution is not None and last_trace is not None:
            return all_statuses, last_execution, last_trace
        if repair_failed or decision_failed:
            return all_statuses, last_execution, last_trace

        time.sleep(5)

    return all_statuses, last_execution, last_trace


def fetch_remote_repo_state(args: argparse.Namespace, vm_state: VmState) -> tuple[str, str]:
    config_abs_path = f"{vm_state.repo_path.rstrip('/')}/{vm_state.config_file_path.lstrip('/')}"
    revision = ssh_command(args, f"git -C {shlex.quote(vm_state.repo_path)} rev-parse HEAD", timeout=15, use_sudo=True)
    config_text = ssh_command(args, f"cat {shlex.quote(config_abs_path)}", timeout=15, use_sudo=True)
    return revision, config_text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the real Azure -> VM phoe-nix POC path: upload a fake log through token_service/blob storage, "
            "observe log_router -> analysis_agent(OpenCode) -> decision_agent -> final-decisions, then wait for the "
            "real VM local_agent to repair configuration.nix with Ollama and push the repo change."
        )
    )
    parser.add_argument("--project-name", default=os.environ.get("PROJECT_NAME", DEFAULT_PROJECT_NAME))
    parser.add_argument("--env", default=os.environ.get("ENV", DEFAULT_ENV))
    parser.add_argument("--resource-group", default=os.environ.get("RG"))
    parser.add_argument("--servicebus-namespace", default=os.environ.get("SB_NAMESPACE"))
    parser.add_argument("--cosmos-account", default=os.environ.get("COSMOS_ACCOUNT"))
    parser.add_argument("--cosmos-database", default=os.environ.get("COSMOS_DATABASE_NAME", DEFAULT_COSMOS_DB))
    parser.add_argument("--token-app", default=os.environ.get("TOKEN_APP"))
    parser.add_argument("--node-id", default=os.environ.get("NODE_ID", DEFAULT_NODE_ID))
    parser.add_argument("--node-api-key", default=os.environ.get("NODE_API_KEY") or os.environ.get("TF_VAR_node_api_key"))
    parser.add_argument("--vm-ssh-target", default=os.environ.get("VM_SSH_TARGET", DEFAULT_VM_SSH_TARGET))
    parser.add_argument("--vm-ssh-port", type=int, default=int(os.environ.get("VM_SSH_PORT", str(DEFAULT_VM_SSH_PORT))))
    parser.add_argument("--vm-env-path", default=os.environ.get("VM_ENV_PATH", DEFAULT_VM_ENV_PATH))
    parser.add_argument("--vm-service-name", default=os.environ.get("VM_SERVICE_NAME", DEFAULT_VM_SERVICE_NAME))
    parser.add_argument("--vm-sudo-password", default=os.environ.get("VM_SUDO_PASSWORD"))
    parser.add_argument("--expected-ollama-model", default=os.environ.get("OLLAMA_MODEL", "gpt-oss:20b"))
    parser.add_argument("--package")
    parser.add_argument(
        "--package-candidates",
        nargs="+",
        default=list(DEFAULT_PACKAGE_CANDIDATES),
        help="Fallback package names to try when --package is not provided.",
    )
    parser.add_argument("--blob-timeout", type=float, default=DEFAULT_BLOB_TIMEOUT_SECONDS)
    parser.add_argument("--topic-timeout", type=float, default=DEFAULT_TOPIC_TIMEOUT_SECONDS)
    parser.add_argument("--repair-timeout", type=float, default=DEFAULT_REPAIR_TIMEOUT_SECONDS)
    parser.add_argument("--skip-smoke-test", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT_DIR / ".env")
    args = parse_args()

    resource_group = args.resource_group or f"rg-{args.project_name}-{args.env}"
    token_app = args.token_app or f"func-{args.project_name}-{args.env}-token"
    cosmos_account = args.cosmos_account or f"cosmos-{args.project_name}-{args.env}"
    namespace = resolve_sb_namespace(args.project_name, args.env, args.servicebus_namespace)

    if not args.node_api_key:
        raise VerificationError("NODE_API_KEY / TF_VAR_node_api_key is required")

    if not args.skip_smoke_test:
        run_smoke_test(args.node_id, args.node_api_key)

    vm_state = fetch_vm_state(args)
    package = select_test_package(args, vm_state)
    print(f"Selected test package: {package}")

    servicebus_connection = resolve_servicebus_connection(resource_group, namespace)
    cosmos = resolve_cosmos_handles(resource_group, cosmos_account, args.cosmos_database)
    token_key = resolve_token_function_key(resource_group, token_app)
    token_url = f"https://{token_app}.azurewebsites.net/api/token?code={token_key}"

    run_id = uuid.uuid4().hex[:12]
    entry = build_fake_log_entry(package=package, hostname=vm_state.hostname, run_id=run_id)
    payload = build_log_payload(node_id=args.node_id, entries=[entry])

    analysis_input_debug_sub = f"e2e-ai-{run_id}"
    analysis_results_debug_sub = f"e2e-ar-{run_id}"
    final_decisions_debug_sub = f"e2e-fd-{run_id}"
    blob_path = ""
    target_message_id = ""
    start_epoch = int(time.time()) - 60

    try:
        print("== Creating debug subscriptions ==")
        create_subscription(resource_group, namespace, "analysis-input", analysis_input_debug_sub)
        create_subscription(resource_group, namespace, "analysis-results", analysis_results_debug_sub)
        create_subscription(resource_group, namespace, "final-decisions", final_decisions_debug_sub)

        print("== Uploading fake logs through token_service ==")
        token_response = call_token_service(
            token_url=token_url,
            node_id=args.node_id,
            node_api_key=args.node_api_key,
            timeout_seconds=30.0,
        )
        sas_url = str(token_response["sas_url"])
        blob_path = str(token_response["blob_path"])
        target_message_id = f"{blob_path}:0"
        print(f"token_service issued blob path: {blob_path}")
        upload_blob_via_sas(sas_url=sas_url, payload=payload, timeout_seconds=30.0)
        print("Uploaded fake log batch to blob storage")

        print("== Waiting for log_router -> analysis-input ==")
        analysis_input_message = receive_specific_message(
            servicebus_connection=servicebus_connection,
            topic_name="analysis-input",
            subscription_name=analysis_input_debug_sub,
            wanted_message_id=target_message_id,
            timeout_seconds=args.blob_timeout,
        )
        analysis_input_payload = json.loads(message_body_bytes(analysis_input_message).decode("utf-8"))
        print(json.dumps({
            "stage": "analysis-input",
            "message_id": getattr(analysis_input_message, "message_id", None),
            "node_id": analysis_input_payload.get("node_id"),
            "unit": analysis_input_payload.get("unit"),
            "message": analysis_input_payload.get("message"),
        }, indent=2))

        print("== Waiting for analysis_agent(OpenCode) -> analysis-results ==")
        analysis_results_message = receive_specific_message(
            servicebus_connection=servicebus_connection,
            topic_name="analysis-results",
            subscription_name=analysis_results_debug_sub,
            wanted_message_id=target_message_id,
            timeout_seconds=args.topic_timeout,
        )
        analysis_result_payload = json.loads(message_body_bytes(analysis_results_message).decode("utf-8"))
        print(json.dumps({
            "stage": "analysis-results",
            "analysis_id": analysis_result_payload.get("analysis_id"),
            "suggested_action": analysis_result_payload.get("suggested_action"),
            "remediation_hint": analysis_result_payload.get("remediation_hint"),
            "analysis_summary": analysis_result_payload.get("analysis_summary"),
        }, indent=2))

        print("== Waiting for decision_agent -> final-decisions ==")
        decision_message = receive_matching_message(
            servicebus_connection=servicebus_connection,
            topic_name="final-decisions",
            subscription_name=final_decisions_debug_sub,
            timeout_seconds=args.topic_timeout,
            matcher=lambda message: json.loads(message_body_bytes(message).decode("utf-8")).get("analysis_id")
            == target_message_id,
            wanted_description=f"decision with analysis_id={target_message_id}",
        )
        decision_payload = json.loads(message_body_bytes(decision_message).decode("utf-8"))
        decision_id = str(decision_payload.get("decision_id", ""))
        action = str(decision_payload.get("action", ""))
        remediation_text = str(decision_payload.get("remediation_text", ""))
        print(json.dumps({
            "stage": "final-decisions",
            "decision_id": decision_id,
            "analysis_id": decision_payload.get("analysis_id"),
            "action": action,
            "remediation_text": remediation_text,
        }, indent=2))
        if not decision_id:
            raise VerificationError("decision_agent produced a decision without decision_id")
        if action not in {"apply_config", "rebuild"}:
            raise VerificationError(f"Expected decision action 'apply_config' or 'rebuild', got {action!r}")
        if package not in remediation_text and f"pkgs.{package}" not in remediation_text:
            print(f"WARN: remediation_text did not explicitly mention package {package!r}")

        statuses, execution_result, repair_trace = poll_local_agent(
            cosmos=cosmos,
            decision_id=decision_id,
            node_id=args.node_id,
            since_epoch=start_epoch,
            timeout_seconds=args.repair_timeout,
        )
        snapshot = fetch_decision_cosmos_snapshot(
            cosmos=cosmos,
            decision_id=decision_id,
            node_id=args.node_id,
            since_epoch=start_epoch,
        )
        print_decision_cosmos_snapshot(snapshot, decision_id=decision_id)

        after_revision, after_config_text = fetch_remote_repo_state(args, vm_state)
        success_seen = any(
            item.get("stage") == "repair" and item.get("status") == "completed" for item in statuses
        )
        repair_failed = any(
            item.get("stage") == "repair" and item.get("status") == "failed" for item in statuses
        )
        decision_failed = any(
            item.get("stage") == "decision" and item.get("status") in {"failed", "blocked"}
            for item in statuses
        )
        if not success_seen:
            terminal_statuses = [(item.get("stage"), item.get("status")) for item in statuses]
            if repair_failed or decision_failed:
                raise VerificationError(
                    f"Decision {decision_id} ended without repair/completed. Terminal statuses: {terminal_statuses}"
                )
            raise VerificationError(
                f"Timed out waiting for repair/completed for decision {decision_id}. Last statuses: {terminal_statuses}"
            )
        if execution_result is None or execution_result.get("success") is not True:
            raise VerificationError(f"execution-results for {decision_id} was missing or unsuccessful")
        if repair_trace is None or repair_trace.get("push_success") is not True:
            raise VerificationError(f"repair-trace for {decision_id} was missing or push_success was not true")
        if after_revision == vm_state.before_revision:
            raise VerificationError("VM repo HEAD did not change after repair")
        if package not in after_config_text and f"pkgs.{package}" not in after_config_text:
            raise VerificationError(f"VM config did not contain the expected package marker for {package!r}")

        print("== PASS: real Azure -> VM end-to-end run succeeded ==")
        print(json.dumps({
            "node_id": args.node_id,
            "run_id": run_id,
            "blob_path": blob_path,
            "analysis_id": target_message_id,
            "decision_id": decision_id,
            "package": package,
            "repo_revision_before": vm_state.before_revision,
            "repo_revision_after": after_revision,
            "push_success": repair_trace.get("push_success"),
            "repair_attempt": repair_trace.get("attempt_number"),
            "execution_success": execution_result.get("success"),
        }, indent=2))
        return 0
    finally:
        print("== Cleaning up debug subscriptions ==")
        delete_subscription(resource_group, namespace, "analysis-input", analysis_input_debug_sub)
        delete_subscription(resource_group, namespace, "analysis-results", analysis_results_debug_sub)
        delete_subscription(resource_group, namespace, "final-decisions", final_decisions_debug_sub)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
    except VerificationError as error:
        print(f"FAIL: {error}", file=sys.stderr)
        raise SystemExit(1)
