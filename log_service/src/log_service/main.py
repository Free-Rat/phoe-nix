import argparse
import signal
import select

from systemd import journal

from log_service.config import load_config
from log_service.storage import build_log_payload, upload_log_payload
from log_service.token_client import TokenServiceError, request_storage_token

running = True


def parse_args():
    parser = argparse.ArgumentParser(description="Log Service - Collects logs from NixOS nodes")
    parser.add_argument(
        "-s",
        "--services",
        nargs="+",
        metavar="SERVICE",
        help="Filter logs by service name(s). If not specified, all logs are shown.",
    )
    return parser.parse_args()


def signal_handler(signum, frame):
    global running
    sig_name = signal.Signals(signum).name
    print(f"\nReceived {sig_name}, shutting down gracefully...")
    running = False


def process_entry(entry: dict[str, object], *, config) -> None:
    message = str(entry.get("MESSAGE", ""))
    if not message:
        return

    print(f"{entry['__REALTIME_TIMESTAMP']} {message}")

    # Request a blob-scoped upload token for this entry, then upload the raw payload directly.
    token_response = request_storage_token(
        config.token_service_url,
        node_id=config.node_id,
        node_api_key=config.node_api_key,
        timeout_seconds=config.upload_timeout_seconds,
    )
    payload = build_log_payload(entry, node_id=config.node_id)
    upload_log_payload(token_response, payload, timeout_seconds=config.upload_timeout_seconds)
    print(f"successful save to {token_response.blob_path}")


def main():
    global running
    args = parse_args()
    config = load_config()
    service_filter = args.services

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    journal_reader = journal.Reader()
    journal_reader.log_level(journal.LOG_INFO)

    if service_filter:
        for service_name in service_filter:
            journal_reader.add_match(_SYSTEMD_UNIT=f"{service_name}.service")

    journal_reader.seek_tail()
    journal_reader.get_previous()

    poller = select.poll()
    poller.register(journal_reader, journal_reader.get_events())

    while running and poller.poll():
        if journal_reader.process() != journal.APPEND:
            continue

        for entry in journal_reader:
            try:
                process_entry(entry, config=config)
            except TokenServiceError as error:
                print(f"failed to fetch upload token: {error}")
            except Exception as error:
                # Keep the journal loop alive even if a single upload fails.
                print(f"failed to upload log entry: {error}")

    print("Log service stopped.")


if __name__ == "__main__":
    main()
