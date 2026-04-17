import argparse
import signal
import select
import sys
from systemd import journal

running = True

def get_storage_token() -> str:
    return "example_token"

def save_to_storage(token: str) -> bool:
    return True

def parse_args():
    parser = argparse.ArgumentParser(description="Log Service - Collects logs from NixOS nodes")
    parser.add_argument(
        "-s", "--services",
        nargs="+",
        metavar="SERVICE",
        help="Filter logs by service name(s). If not specified, all logs are shown."
    )
    return parser.parse_args()

def signal_handler(signum, frame):
    global running
    sig_name = signal.Signals(signum).name
    print(f"\nReceived {sig_name}, shutting down gracefully...")
    running = False

def main():
    global running
    args = parse_args()
    service_filter = args.services

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    j = journal.Reader()
    j.log_level(journal.LOG_INFO)

    if service_filter:
        for svc in service_filter:
            j.add_match(_SYSTEMD_UNIT=f"{svc}.service")

    j.seek_tail()
    j.get_previous()

    p = select.poll()
    p.register(j, j.get_events())

    while running and p.poll():
        if j.process() != journal.APPEND:
            continue

        for entry in j:
            if entry['MESSAGE'] != "":
                print(str(entry['__REALTIME_TIMESTAMP']) + ' ' + entry['MESSAGE'])
                tk = get_storage_token()
                success = save_to_storage(tk)
                if success:
                    print("successful save to <url>")

    print("Log service stopped.")


if __name__ == "__main__":
    main()
