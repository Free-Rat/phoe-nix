#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from urllib import request


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Publish a decision to mock Azure Service Bus")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--topic", default="final-decisions")
    parser.add_argument("--node-id", default="nixos")
    parser.add_argument("--decision-id", default="mock-decision")
    parser.add_argument("--analysis-id", default="mock-analysis")
    parser.add_argument(
        "--analysis-summary", default="SSH connectivity is failing; inspect configuration.nix and repair it."
    )
    parser.add_argument(
        "--remediation-text",
        default="Enable the necessary SSH-related configuration in configuration.nix.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    decision = {
        "schema_version": "1.0",
        "decision_id": args.decision_id,
        "node_id": args.node_id,
        "analysis_id": args.analysis_id,
        "action": "apply_config",
        "command": "",
        "severity": "critical",
        "confidence": 0.9,
        "analysis_summary": args.analysis_summary,
        "remediation_text": args.remediation_text,
        "idempotency_key": args.decision_id,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    payload = {
        "body": json.dumps(decision),
        "message_id": args.decision_id,
        "content_type": "application/json",
    }
    http_request = request.Request(
        f"{args.base_url.rstrip('/')}/servicebus/topics/{args.topic}/publish",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with request.urlopen(http_request, timeout=10) as response:
        print(response.read().decode("utf-8"))


if __name__ == "__main__":
    main()
