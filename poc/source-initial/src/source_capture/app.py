from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyotp
from dotenv import load_dotenv
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2

from source_capture.metrics import SessionMetrics, session_period
from source_capture.paths import ENV_FILES, POC_ROOT, RAW_DIR, RUNS_DIR

IST = ZoneInfo("Asia/Kolkata")
LTP_MODE = 1
NSE_CM = 1


class CaptureWebSocket(SmartWebSocketV2):
    """Records wire frame size before the SDK parses a binary packet."""

    def __init__(self, *args, frame_handler=None, text_handler=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.frame_handler = frame_handler
        self.text_handler = text_handler

    def _on_data(self, wsapp, data, data_type, continue_flag):
        if data == "pong" or data_type == 1 and data == "pong":
            if self.frame_handler is not None:
                self.frame_handler(heartbeat=True)
            return
        if data_type != 2:
            return
        wire_bytes = len(data) if data is not None else 0
        receive_wall = time.time()
        receive_mono = time.monotonic_ns()
        parsed = None
        parse_error = False
        try:
            parsed = self._parse_binary_data(data)
        except Exception:
            parse_error = True
        if self.frame_handler is not None:
            self.frame_handler(
                wire_bytes=wire_bytes,
                parsed=parsed,
                parse_error=parse_error,
                receive_wall=receive_wall,
                receive_mono_ns=receive_mono,
            )
        if parsed is not None and not parse_error:
            self.on_data(wsapp, parsed)

    def _on_close(self, wsapp, *_args):
        self.on_close(wsapp)

    def _on_message(self, wsapp, message):
        if isinstance(message, str):
            if message == "pong":
                if self.frame_handler is not None:
                    self.frame_handler(heartbeat=True)
                return
            if self.text_handler is not None:
                self.text_handler(message)
            return
        super()._on_message(wsapp, message)


def parse_tokens(raw: str) -> list[str]:
    tokens = [part.strip() for part in raw.split(",") if part.strip()]
    if not tokens:
        raise SystemExit("TOKENS is empty. Set comma-separated NSE tokens in .env")
    return tokens


def load_token_file(path: Path) -> list[str]:
    tokens: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.append(line.split()[0])
    if not tokens:
        raise SystemExit(f"No tokens in {path}")
    return tokens


MODE_NAMES = {1: "LTP", 2: "QUOTE", 3: "SNAP_QUOTE"}


def login() -> tuple[str, str, str]:
    api_key = os.environ.get("ANGEL_API_KEY", "").strip()
    client_code = os.environ.get("ANGEL_CLIENT_CODE", "").strip()
    pin = os.environ.get("ANGEL_PIN", "").strip()
    totp_secret = os.environ.get("ANGEL_TOTP_SECRET", "").strip()
    totp_once = os.environ.get("ANGEL_TOTP", "").strip()
    if not all([api_key, client_code, pin]):
        raise SystemExit("Set ANGEL_API_KEY, ANGEL_CLIENT_CODE, and ANGEL_PIN in .env")
    if totp_once:
        totp = totp_once
    elif totp_secret:
        totp = pyotp.TOTP(totp_secret).now()
    else:
        raise SystemExit("Set ANGEL_TOTP_SECRET or ANGEL_TOTP in .env")

    client = SmartConnect(api_key=api_key)
    session = client.generateSession(client_code, pin, totp)
    if not session or session.get("status") is False:
        message = session.get("message") if isinstance(session, dict) else "unknown"
        raise SystemExit(f"Login failed: {message}")
    data = session.get("data") or {}
    jwt = data.get("jwtToken") or data.get("jwt_token")
    feed = client.getfeedToken() or data.get("feedToken")
    if not jwt or not feed:
        raise SystemExit(f"Login succeeded but jwt/feed token missing: {list(data.keys())}")
    return jwt, feed, api_key


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Capture Angel One websocket source metrics")
    parser.add_argument("--duration", type=int, default=600, help="Seconds to capture")
    parser.add_argument("--tokens", default="", help="Comma-separated tokens (overrides file and .env)")
    parser.add_argument("--tokens-file", default="", help="Token file, one token per line")
    parser.add_argument("--mode", type=int, default=LTP_MODE, choices=(1, 2, 3), help="1 LTP, 2 Quote, 3 SnapQuote")
    parser.add_argument("--label", default="capture", help="Run name prefix")
    parser.add_argument("--drop-after", type=int, default=0, help="Force-close the socket after N seconds")
    parser.add_argument(
        "--dump-ticks",
        action="store_true",
        help="Write every parsed tick to ticks.jsonl in the run directory",
    )
    return parser


def resolve_tokens(args: argparse.Namespace) -> list[str]:
    if args.tokens.strip():
        return parse_tokens(args.tokens)
    if args.tokens_file.strip():
        path = Path(args.tokens_file)
        if not path.is_absolute() and not path.exists():
            path = POC_ROOT / path
        return load_token_file(path)
    env_tokens = os.environ.get("TOKENS", "")
    if env_tokens.strip():
        return parse_tokens(env_tokens)
    raise SystemExit("Pass --tokens-file, --tokens, or set TOKENS")


def main() -> int:
    for env_file in ENV_FILES:
        load_dotenv(env_file)
    args = build_parser().parse_args()
    tokens = resolve_tokens(args)
    client_code = os.environ.get("ANGEL_CLIENT_CODE", "").strip()
    mode_name = MODE_NAMES.get(args.mode, str(args.mode))

    run_id = datetime.now(IST).strftime("%Y%m%dT%H%M%S")
    run_dir = RUNS_DIR / f"{args.label}_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    csv_path = run_dir / "metrics_1s.csv"

    metrics = SessionMetrics(
        tokens=tokens,
        duration_s=args.duration,
        csv_path=csv_path,
        started_wall=time.time(),
        started_mono_ns=time.monotonic_ns(),
    )
    metrics.open_csv()

    tick_path = RAW_DIR / run_dir.name / "ticks.jsonl"
    tick_file = None
    if args.dump_ticks:
        tick_path.parent.mkdir(parents=True, exist_ok=True)
        tick_file = tick_path.open("w", encoding="utf-8")
    tick_lock = threading.Lock()
    ticks_written = 0

    stop = threading.Event()
    ws_holder: dict[str, CaptureWebSocket | None] = {"ws": None}
    started_at = datetime.now(IST)

    def frame_handler(
        *,
        heartbeat: bool = False,
        wire_bytes: int = 0,
        parsed: dict | None = None,
        parse_error: bool = False,
        receive_wall: float = 0.0,
        receive_mono_ns: int = 0,
    ) -> None:
        if heartbeat:
            metrics.on_heartbeat()
            return
        metrics.on_frame(
            receive_wall=receive_wall,
            receive_mono_ns=receive_mono_ns,
            wire_bytes=wire_bytes,
            parsed=parsed,
            parse_error=parse_error,
        )
        if tick_file is not None and parsed is not None and not parse_error:
            nonlocal ticks_written
            line = json.dumps(
                {
                    "receive_ts": receive_wall,
                    "wire_bytes": wire_bytes,
                    "event": parsed,
                },
                default=str,
            )
            with tick_lock:
                tick_file.write(line + "\n")
                ticks_written += 1
                if ticks_written % 200 == 0:
                    tick_file.flush()

    def on_text(message: str) -> None:
        metrics.note_subscribe_error(message)
        print(f"server text: {message[:300]}", flush=True)

    def on_open(_wsapp) -> None:
        metrics.on_connect()
        ws = ws_holder["ws"]
        if ws is None:
            return
        # The SDK keeps the token list by reference and appends on every subscribe.
        # Replace it so a reconnect cannot double 1000 into 2000, 4000, ...
        ws.input_request_dict.pop(args.mode, None)
        ws.subscribe(
            "srcstudy1",
            args.mode,
            [{"exchangeType": NSE_CM, "tokens": list(tokens)}],
        )
        now = datetime.now(IST)
        print(
            f"subscribed mode={mode_name} n={len(tokens)} period={session_period(now)} duration_s={args.duration}",
            flush=True,
        )

    def on_data(_wsapp, _message) -> None:
        return

    def on_error(*_args) -> None:
        metrics.on_error()
        print(f"websocket error: {_args}", flush=True)

    def on_close(*_args) -> None:
        metrics.on_disconnect()
        print("websocket closed", flush=True)

    def shutdown(_signum=None, _frame=None) -> None:
        stop.set()
        ws = ws_holder["ws"]
        if ws is not None:
            try:
                ws.close_connection()
            except Exception:
                pass

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print(f"logging in to SmartAPI for {args.label}…", flush=True)
    jwt, feed, api_key = login()
    print("login ok; opening websocket", flush=True)

    ws = CaptureWebSocket(
        jwt,
        api_key,
        client_code,
        feed,
        max_retry_attempt=5,
        retry_strategy=0,
        retry_delay=5,
        frame_handler=frame_handler,
        text_handler=on_text,
    )
    ws_holder["ws"] = ws
    ws.on_open = on_open
    ws.on_data = on_data
    ws.on_error = on_error
    ws.on_close = on_close

    def time_limit() -> None:
        deadline = time.time() + args.duration
        while not stop.is_set():
            remaining = deadline - time.time()
            if remaining <= 0:
                print(f"{args.duration}s elapsed; stopping", flush=True)
                shutdown()
                return
            stop.wait(min(remaining, 5))

    def force_drop() -> None:
        if args.drop_after <= 0:
            return
        if stop.wait(args.drop_after):
            return
        metrics.note_forced_drop()
        print(f"forcing socket drop after {args.drop_after}s", flush=True)
        current = ws_holder["ws"]
        sock = getattr(getattr(current, "wsapp", None), "sock", None)
        if sock is not None:
            try:
                sock.close()
            except Exception as exc:
                print(f"socket drop failed: {exc}", flush=True)

    threading.Thread(target=time_limit, name="duration", daemon=True).start()
    threading.Thread(target=force_drop, name="drop", daemon=True).start()

    try:
        ws.connect()
    except Exception as exc:
        print(f"websocket connect failed: {exc}", file=sys.stderr)
    finally:
        stop.set()
        if tick_file is not None:
            with tick_lock:
                tick_file.flush()
                tick_file.close()
        snap = metrics.export_run(
            run_dir,
            meta={
                "label": args.label,
                "mode": args.mode,
                "mode_name": mode_name,
                "started_ist": started_at.isoformat(),
                "ended_ist": datetime.now(IST).isoformat(),
                "period": session_period(started_at),
                "drop_after_s": args.drop_after,
                "ticks_file": str(tick_path.relative_to(POC_ROOT)) if args.dump_ticks else "",
                "ticks_written": ticks_written,
            },
        )
        print(
            f"wrote {run_dir} events={snap['events']} ticks_written={ticks_written} peak_1s={snap['peak_events_per_sec']}",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
