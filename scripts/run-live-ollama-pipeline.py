#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib import request

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR / "analysis_agent" / "src"))
sys.path.insert(0, str(ROOT_DIR / "schemas" / "src"))

from azure.servicebus import ServiceBusClient, ServiceBusMessage  # type: ignore[import-not-found]
from azure.storage.blob import BlobClient  # type: ignore[import-not-found]

from analysis_agent.config import AnalysisAgentConfig
from analysis_agent.message_handler import analyze_message


DEFAULT_PROJECT_NAME = "project-healer"
DEFAULT_ENV = "dev"
DEFAULT_NODE_ID = "nixos"
DEFAULT_MODEL = "gpt-oss:20b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/chat"


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key, value)


def run_command(command: list[str], *, capture_output: bool = True) -> str:
    result = subprocess.run(command, check=True, text=True, capture_output=capture_output)
    return result.stdout.strip() if capture_output else ""


def run_az(*args: str, capture_output: bool = True) -> str:
    return run_command(["az", *args], capture_output=capture_output)


def resolve_tenant_suffix() -> str:
    explicit = os.environ.get("AZURE_TENANT_SUFFIX")
    if explicit:
        return explicit
    tenant_id = run_az("account", "show", "--query", "tenantId", "-o", "tsv")
    if not tenant_id:
        raise RuntimeError("Azure CLI returned an empty tenantId")
    suffix = tenant_id.replace("-", "")[:6]
    os.environ["AZURE_TENANT_SUFFIX"] = suffix
    return suffix


def resolve_sb_namespace(project_name: str, env_name: str) -> str:
    explicit = os.environ.get("SB_NAMESPACE")
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
    )
    if not key:
        raise RuntimeError("Unable to resolve Service Bus shared access key")
    connection = (
        f"Endpoint=sb://{namespace}.servicebus.windows.net/;"
        f"SharedAccessKeyName={auth_rule};SharedAccessKey={key}"
    )
    os.environ["SERVICEBUS_CONNECTION"] = connection
    return connection


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


def canned_entries(case_name: str, *, hostname: str) -> tuple[list[dict[str, Any]], str]:
    now_us = int(time.time() * 1_000_000)
    if case_name == "sshd":
        return (
            [
                {
                    "__REALTIME_TIMESTAMP": str(now_us),
                    "MESSAGE": (
                        "systemd[1]: sshd.service: Failed to start because the OpenSSH service is disabled in the "
                        "current NixOS configuration. This node should accept SSH connections for administration."
                    ),
                    "_SYSTEMD_UNIT": "sshd.service",
                    "PRIORITY": "3",
                    "_HOSTNAME": hostname,
                    "SYSLOG_IDENTIFIER": "systemd",
                },
                {
                    "__REALTIME_TIMESTAMP": str(now_us + 1000),
                    "MESSAGE": "sshd.service entered failed state after configuration evaluation.",
                    "_SYSTEMD_UNIT": "sshd.service",
                    "PRIORITY": "3",
                    "_HOSTNAME": hostname,
                    "SYSLOG_IDENTIFIER": "systemd",
                },
            ],
            "services.openssh.enable",
        )

    return (
        [
            {
                "__REALTIME_TIMESTAMP": str(now_us),
                "MESSAGE": (
                    "bootstrap-banner.sh[421]: /etc/local/bin/bootstrap-banner.sh: line 12: cowsay: command not found. "
                    "The node's startup banner depends on cowsay being installed system-wide."
                ),
                "_SYSTEMD_UNIT": "bootstrap-banner.service",
                "PRIORITY": "3",
                "_HOSTNAME": hostname,
                "SYSLOG_IDENTIFIER": "bootstrap-banner.sh",
            },
            {
                "__REALTIME_TIMESTAMP": str(now_us + 1000),
                "MESSAGE": "bootstrap-banner.service: Main process exited, code=exited, status=127/n/a",
                "_SYSTEMD_UNIT": "bootstrap-banner.service",
                "PRIORITY": "3",
                "_HOSTNAME": hostname,
                "SYSLOG_IDENTIFIER": "systemd",
            },
        ],
        "cowsay",
    )


def extract_text_from_ollama_response(response_body: str) -> str:
    payload = json.loads(response_body)
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
        response_text = payload.get("response")
        if isinstance(response_text, str):
            return response_text.strip()
    return response_body.strip()


def call_ollama_api(*, api_url: str, api_key: str, model: str, prompt: str, timeout_seconds: float) -> str:
    del api_key
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "system",
                "content": (
                    "You analyze NixOS operational issues. Reply with exactly one JSON object and no markdown. "
                    "Include fields such as error_type, severity, root_cause, suggested_action, confidence, "
                    "analysis_text, remediation_hint, and affected_unit when supported by the evidence."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    req = request.Request(
        api_url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with request.urlopen(req, timeout=timeout_seconds) as response:
        return extract_text_from_ollama_response(response.read().decode("utf-8"))


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
    matcher: Callable[[Any], bool],
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


def receive_first_message(
    *,
    servicebus_connection: str,
    topic_name: str,
    subscription_name: str,
    timeout_seconds: float,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    with ServiceBusClient.from_connection_string(servicebus_connection) as client:
        receiver = client.get_subscription_receiver(topic_name=topic_name, subscription_name=subscription_name)
        with receiver:
            while time.monotonic() < deadline:
                batch = receiver.receive_messages(max_message_count=10, max_wait_time=5)
                if not batch:
                    continue
                message = batch[0]
                receiver.complete_message(message)
                return message
    raise TimeoutError(f"Timed out waiting for message on {topic_name}/{subscription_name}")


def send_topic_message(
    *,
    servicebus_connection: str,
    topic_name: str,
    message_id: str,
    body: str,
    application_properties: dict[str, Any] | None = None,
) -> None:
    with ServiceBusClient.from_connection_string(servicebus_connection) as client:
        sender = client.get_topic_sender(topic_name=topic_name)
        with sender:
            sender.send_messages(
                ServiceBusMessage(
                    body,
                    content_type="application/json",
                    message_id=message_id,
                    application_properties=application_properties or {},
                )
            )


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
            capture_output=False,
        )
    except subprocess.CalledProcessError:
        pass


def stop_function_app(resource_group: str, app_name: str) -> None:
    run_az("functionapp", "stop", "--resource-group", resource_group, "--name", app_name, capture_output=False)


def start_function_app(resource_group: str, app_name: str) -> None:
    run_az("functionapp", "start", "--resource-group", resource_group, "--name", app_name, capture_output=False)


def analyze_with_ollama(*, raw_body: bytes, message_id: str, model: str, api_url: str, timeout_seconds: float) -> dict[str, Any]:
    config = AnalysisAgentConfig(
        servicebus_connection="unused",
        analysis_results_topic_name="analysis-results",
        keyvault_name="unused",
        opencode_api_key_secret="unused",
        opencode_api_url=api_url,
        opencode_model=model,
        ai_timeout_seconds=timeout_seconds,
    )
    result = analyze_message(
        raw_body=raw_body,
        message_id=message_id,
        config=config,
        read_secret_value=lambda *_args: "",
        model_caller=call_ollama_api,
    )
    return result.model_dump(mode="json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Trigger the live Azure blob -> log_router -> analysis-input path, run the analysis step locally "
            "on this host against Ollama, publish the AnalysisResult back to Azure, and wait for the real "
            "decision_agent to emit a Decision."
        )
    )
    parser.add_argument("--entry-point", choices=["blob", "analysis-input"], default="blob")
    parser.add_argument("--case", choices=["cowsay", "sshd"], default="cowsay")
    parser.add_argument("--node-id", default=os.environ.get("NODE_ID", DEFAULT_NODE_ID))
    parser.add_argument("--hostname", default=os.environ.get("HOSTNAME", "malenia"))
    parser.add_argument("--project-name", default=os.environ.get("PROJECT_NAME", DEFAULT_PROJECT_NAME))
    parser.add_argument("--env", default=os.environ.get("ENV", DEFAULT_ENV))
    parser.add_argument("--resource-group", default=os.environ.get("RG"))
    parser.add_argument("--token-app", default=os.environ.get("TOKEN_APP"))
    parser.add_argument("--analysis-app", default=os.environ.get("ANALYSIS_APP"))
    parser.add_argument("--servicebus-namespace", default=os.environ.get("SB_NAMESPACE"))
    parser.add_argument("--node-api-key", default=os.environ.get("NODE_API_KEY") or os.environ.get("TF_VAR_node_api_key"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
    parser.add_argument("--model", default=os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL))
    parser.add_argument("--analysis-timeout", type=float, default=60.0)
    parser.add_argument("--message-timeout", type=float, default=90.0)
    parser.add_argument(
        "--blob-message-timeout",
        type=float,
        default=float(os.environ.get("BLOB_MESSAGE_TIMEOUT", "600")),
        help=(
            "How long to wait for blob -> log_router -> analysis-input. Defaults to 600s because "
            "blob triggers on the Y1 consumption plan can take several minutes to fire."
        ),
    )
    parser.add_argument("--keep-analysis-app-running", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv(ROOT_DIR / ".env")
    args = parse_args()

    resource_group = args.resource_group or f"rg-{args.project_name}-{args.env}"
    token_app = args.token_app or f"func-{args.project_name}-{args.env}-token"
    analysis_app = args.analysis_app or f"func-{args.project_name}-{args.env}-analysis"
    namespace = args.servicebus_namespace or resolve_sb_namespace(args.project_name, args.env)

    if not args.node_api_key:
        raise RuntimeError("NODE_API_KEY / TF_VAR_node_api_key is required")

    servicebus_connection = resolve_servicebus_connection(resource_group, namespace)
    token_key = resolve_token_function_key(resource_group, token_app)
    token_url = f"https://{token_app}.azurewebsites.net/api/token?code={token_key}"

    analysis_input_debug_sub = f"ollama-ai-{uuid.uuid4().hex[:8]}"
    analysis_results_debug_sub = f"ollama-ar-{uuid.uuid4().hex[:8]}"
    final_decisions_debug_sub = f"ollama-fd-{uuid.uuid4().hex[:8]}"
    should_restart_analysis_app = False
    reset_analysis_subscription = False

    entries, expected_hint = canned_entries(args.case, hostname=args.hostname)
    payload = build_log_payload(node_id=args.node_id, entries=entries)

    try:
        print(f"Creating debug subscriptions in {namespace} ...")
        create_subscription(resource_group, namespace, "analysis-input", analysis_input_debug_sub)
        create_subscription(resource_group, namespace, "analysis-results", analysis_results_debug_sub)
        create_subscription(resource_group, namespace, "final-decisions", final_decisions_debug_sub)

        if not args.keep_analysis_app_running:
            print(f"Stopping deployed analysis app {analysis_app} to avoid a race with the local Ollama driver ...")
            stop_function_app(resource_group, analysis_app)
            should_restart_analysis_app = True

        if args.entry_point == "blob":
            print("Requesting upload SAS from token_service ...")
            token_response = call_token_service(
                token_url=token_url,
                node_id=args.node_id,
                node_api_key=args.node_api_key,
                timeout_seconds=30.0,
            )
            sas_url = str(token_response["sas_url"])
            blob_path = str(token_response["blob_path"])
            target_message_id = f"{blob_path}:0"
            print(f"Uploading test batch to blob storage at {blob_path} ...")
            upload_blob_via_sas(sas_url=sas_url, payload=payload, timeout_seconds=30.0)
            print(
                "Waiting for log_router to publish the normalized log to "
                f"analysis-input/{analysis_input_debug_sub} (timeout: {args.blob_message_timeout:.0f}s) ..."
            )
            analysis_input_message = receive_specific_message(
                servicebus_connection=servicebus_connection,
                topic_name="analysis-input",
                subscription_name=analysis_input_debug_sub,
                wanted_message_id=target_message_id,
                timeout_seconds=args.blob_message_timeout,
            )
            analysis_input_body = message_body_bytes(analysis_input_message)
            print("Received normalized log from the debug analysis-input subscription.")
        else:
            blob_path = f"logs/{args.node_id}/manual-{uuid.uuid4().hex}"
            target_message_id = f"{blob_path}:0"
            normalized_log = {
                "schema_version": "1.0",
                "node_id": args.node_id,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "message": str(entries[0]["MESSAGE"]),
                "unit": str(entries[0].get("_SYSTEMD_UNIT") or "") or None,
                "priority": int(entries[0].get("PRIORITY") or 3),
                "hostname": args.hostname,
                "source": "log_router",
                "source_identifier": str(entries[0].get("SYSLOG_IDENTIFIER") or "") or None,
                "blob_path": blob_path,
            }
            print(f"Publishing normalized log directly to analysis-input as {target_message_id} ...")
            send_topic_message(
                servicebus_connection=servicebus_connection,
                topic_name="analysis-input",
                message_id=target_message_id,
                body=json.dumps(normalized_log),
                application_properties={"message_kind": "normalized_log"},
            )
            analysis_input_message = receive_specific_message(
                servicebus_connection=servicebus_connection,
                topic_name="analysis-input",
                subscription_name=analysis_input_debug_sub,
                wanted_message_id=target_message_id,
                timeout_seconds=args.message_timeout,
            )
            analysis_input_body = message_body_bytes(analysis_input_message)
            print("Received normalized log from the debug analysis-input subscription.")
        if should_restart_analysis_app:
            reset_analysis_subscription = True

        print(f"Running analysis locally against Ollama {args.model} at {args.ollama_url} ...")
        analysis_result = analyze_with_ollama(
            raw_body=analysis_input_body,
            message_id=target_message_id,
            model=args.model,
            api_url=args.ollama_url,
            timeout_seconds=args.analysis_timeout,
        )
        print(json.dumps(analysis_result, indent=2))

        print("Publishing the AnalysisResult back to Azure ...")
        send_topic_message(
            servicebus_connection=servicebus_connection,
            topic_name="analysis-results",
            message_id=target_message_id,
            body=json.dumps(analysis_result),
            application_properties={"message_kind": "analysis_result"},
        )

        print("Waiting for the published AnalysisResult to land on the debug analysis-results subscription ...")
        analysis_results_message = receive_specific_message(
            servicebus_connection=servicebus_connection,
            topic_name="analysis-results",
            subscription_name=analysis_results_debug_sub,
            wanted_message_id=target_message_id,
            timeout_seconds=args.message_timeout,
        )
        print(message_body_bytes(analysis_results_message).decode("utf-8"))

        print(f"Waiting for decision_agent to publish a Decision for analysis_id={target_message_id} ...")
        decision_message = receive_matching_message(
            servicebus_connection=servicebus_connection,
            topic_name="final-decisions",
            subscription_name=final_decisions_debug_sub,
            timeout_seconds=args.message_timeout,
            matcher=lambda message: json.loads(message_body_bytes(message).decode("utf-8")).get("analysis_id")
            == target_message_id,
            wanted_description=f"decision with analysis_id={target_message_id}",
        )
        decision_body = message_body_bytes(decision_message).decode("utf-8")
        print(decision_body)
        decision_payload = json.loads(decision_body)

        remediation_text = str(decision_payload.get("remediation_text", ""))
        action = str(decision_payload.get("action", ""))
        if expected_hint in remediation_text:
            print(f"PASS: remediation_text contains expected hint: {expected_hint}")
        else:
            print(f"WARN: remediation_text did not contain the expected hint: {expected_hint}")
        print(f"Decision action: {action}")
        return 0
    finally:
        if reset_analysis_subscription:
            print("Resetting analysis-input/analysis-agent so the parked test message is discarded before restart ...")
            delete_subscription(resource_group, namespace, "analysis-input", "analysis-agent")
            create_subscription(resource_group, namespace, "analysis-input", "analysis-agent")
        if should_restart_analysis_app:
            print(f"Restarting analysis app {analysis_app} ...")
            try:
                start_function_app(resource_group, analysis_app)
            except subprocess.CalledProcessError as error:
                print(f"WARN: failed to restart {analysis_app}: {error}", file=sys.stderr)
        print("Deleting debug subscriptions ...")
        delete_subscription(resource_group, namespace, "analysis-input", analysis_input_debug_sub)
        delete_subscription(resource_group, namespace, "analysis-results", analysis_results_debug_sub)
        delete_subscription(resource_group, namespace, "final-decisions", final_decisions_debug_sub)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
