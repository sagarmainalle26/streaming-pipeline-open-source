"""Run the source-study windows and write section 3.4."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from source_capture.paths import POC_ROOT, SRC_DIR, TOKENS_DIR

IST = ZoneInfo("Asia/Kolkata")
PY = sys.executable


def run_capture(
    label: str,
    tokens_file: str,
    duration: int,
    mode: int = 1,
    drop_after: int = 0,
    dump_ticks: bool = False,
) -> int:
    cmd = [
        PY,
        "-m",
        "source_capture",
        "--label",
        label,
        "--tokens-file",
        str(TOKENS_DIR / tokens_file),
        "--duration",
        str(duration),
        "--mode",
        str(mode),
    ]
    if drop_after:
        cmd.extend(["--drop-after", str(drop_after)])
    if dump_ticks:
        cmd.append("--dump-ticks")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(SRC_DIR), env.get("PYTHONPATH", "")]))
    print(f"\n=== {label} mode={mode} duration={duration}s ===", flush=True)
    completed = subprocess.run(cmd, cwd=POC_ROOT, env=env)
    return completed.returncode


def sleep_until(hour: int, minute: int) -> None:
    while True:
        now = datetime.now(IST)
        if (now.hour, now.minute) >= (hour, minute):
            return
        time.sleep(20)


def main() -> int:
    steps = [
        ("structure-ltp", "nse_5.txt", 180, 1, 0, False),
        ("structure-quote", "nse_5.txt", 180, 2, 0, False),
        ("scale-5-ticks", "nse_5.txt", 600, 1, 0, True),
        ("scale-50-ticks", "nse_50.txt", 600, 1, 0, True),
        ("scale-500-ticks", "nse_500.txt", 600, 1, 0, True),
        ("scale-1000-ticks", "nse_1000.txt", 600, 1, 0, True),
        ("reconnect-50", "nse_50.txt", 300, 1, 120, False),
    ]
    for label, tokens_file, duration, mode, drop_after, dump_ticks in steps:
        code = run_capture(label, tokens_file, duration, mode, drop_after, dump_ticks)
        if code != 0:
            print(f"{label} exited {code}", flush=True)

    now = datetime.now(IST)
    if now.hour < 15 or (now.hour == 15 and now.minute < 15):
        print("waiting until 15:00 IST for the close burst window", flush=True)
        sleep_until(15, 0)
        run_capture("burst-close", "nse_50.txt", 900, 1, 0, True)
    else:
        print("close window already passed; burst-close not run", flush=True)

    if not (now.hour == 9 and 15 <= now.minute < 30):
        print("market-open window was not this process; burst-open left for the next session", flush=True)

    from source_capture.report import write_report

    path = write_report()
    print(f"wrote {path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
