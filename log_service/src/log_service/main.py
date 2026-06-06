import argparse
import signal
import select

from systemd import journal

from log_service.config import load_config
from log_service.uploader import BatchUploader

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


def main():
    global running
    args = parse_args()
    config = load_config()
    uploader = BatchUploader(config=config)
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
    uploader.flush()

    while running:
        timeout_ms = max(int(config.flush_interval_seconds * 1000), 1000)
        if not poller.poll(timeout_ms):
            if uploader.flush_due():
                uploader.flush()
            continue

        if journal_reader.process() != journal.APPEND:
            if uploader.flush_due():
                uploader.flush()
            continue

        for entry in journal_reader:
            try:
                process_entry(entry, config=config)
                if uploader.add_entry(dict(entry)):
                    uploader.flush()
            except Exception as error:
                print(f"failed to buffer log entry: {error}")

        if uploader.flush_due():
            uploader.flush()

    uploader.flush()

    print("Log service stopped.")


if __name__ == "__main__":
    main()
