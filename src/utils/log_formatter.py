from datetime import datetime


def simulate_log(msg: str):
    print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}")
