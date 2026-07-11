from __future__ import annotations

import time


def sim_time_to_hhmm(seconds: float | int) -> str:
    seconds = int(seconds)
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h:02d}:{m:02d}"


class DemoLogger:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled

    def line(self, msg: str = "") -> None:
        if self.enabled:
            print(msg, flush=True)

    def banner(self, rows: list[str]) -> None:
        if not self.enabled:
            return
        self.line("=" * 60)
        for row in rows:
            self.line(row)
        self.line("=" * 60)

    def sleep_for_explain(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

