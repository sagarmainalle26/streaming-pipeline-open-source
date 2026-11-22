from __future__ import annotations

import csv
import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MIN_EVENTS_FOR_PROFILE = 100
BURST_WINDOW_NS = 100_000_000
MAX_INTERARRIVAL_SAMPLES = 200_000
MAX_LATENCY_SAMPLES = 200_000
MAX_DUP_KEYS = 1_000_000

CSV_FIELDS = (
    "bucket_start_ist",
    "period",
    "messages",
    "events",
    "bytes",
    "max_message_bytes",
    "invalid",
    "duplicates",
)


def _percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def session_period(ts_ist: datetime) -> str:
    minutes = ts_ist.hour * 60 + ts_ist.minute
    if 9 * 60 <= minutes < 9 * 60 + 15:
        return "pre-market"
    if 9 * 60 + 15 <= minutes < 9 * 60 + 30:
        return "open"
    if 9 * 60 + 30 <= minutes < 14 * 60 + 30:
        return "normal"
    if 14 * 60 + 30 <= minutes < 15 * 60:
        return "high-activity"
    if 15 * 60 <= minutes < 15 * 60 + 30:
        return "close"
    return "outside-cash-hours"


def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


@dataclass
class TokenStat:
    events: int = 0
    last_seq: int | None = None
    last_exchange_ts: int | None = None
    seq_backwards: int = 0
    seq_jumps: int = 0
    exchange_ts_backwards: int = 0
    duplicate_exchange_ts: int = 0
    invalid_ltp: int = 0


def note_event(stat: TokenStat, event: dict) -> None:
    """Update per-token quality counters. Sequence jumps are not treated as proven loss."""
    stat.events += 1
    ltp = event.get("last_traded_price")
    if ltp is None:
        stat.invalid_ltp += 1
    else:
        try:
            if int(ltp) <= 0:
                stat.invalid_ltp += 1
        except (TypeError, ValueError):
            stat.invalid_ltp += 1

    seq = event.get("sequence_number")
    if seq is not None:
        try:
            seq_i = int(seq)
        except (TypeError, ValueError):
            seq_i = None
        if seq_i is not None:
            if stat.last_seq is None:
                stat.last_seq = seq_i
            elif seq_i < stat.last_seq:
                stat.seq_backwards += 1
            elif seq_i > stat.last_seq + 1:
                stat.seq_jumps += 1
                stat.last_seq = seq_i
            else:
                stat.last_seq = seq_i

    exchange_ts = event.get("exchange_timestamp")
    if exchange_ts is not None:
        try:
            ets_i = int(exchange_ts)
        except (TypeError, ValueError):
            ets_i = None
        if ets_i is not None:
            if stat.last_exchange_ts is None:
                stat.last_exchange_ts = ets_i
            elif ets_i < stat.last_exchange_ts:
                stat.exchange_ts_backwards += 1
            elif ets_i == stat.last_exchange_ts:
                stat.duplicate_exchange_ts += 1
            else:
                stat.last_exchange_ts = ets_i


@dataclass
class SecondBucket:
    start_s: int
    messages: int = 0
    events: int = 0
    bytes: int = 0
    max_message_bytes: int = 0
    invalid: int = 0
    duplicates: int = 0


@dataclass
class SessionMetrics:
    tokens: list[str]
    duration_s: int
    csv_path: Path
    started_wall: float
    started_mono_ns: int
    lock: threading.Lock = field(default_factory=threading.Lock)
    current: SecondBucket | None = None
    buckets: list[SecondBucket] = field(default_factory=list)
    csv_writer: csv.DictWriter | None = None
    csv_file: object | None = None

    total_messages: int = 0
    total_events: int = 0
    total_bytes: int = 0
    total_invalid: int = 0
    total_duplicates: int = 0
    total_heartbeats: int = 0
    max_message_bytes: int = 0
    connect_count: int = 0
    disconnect_count: int = 0
    error_count: int = 0
    last_disconnect_mono_ns: int | None = None
    disconnect_duration_s: float = 0.0

    last_message_mono_ns: int | None = None
    interarrival_ns: deque[int] = field(default_factory=lambda: deque(maxlen=MAX_INTERARRIVAL_SAMPLES))
    latency_ms: deque[float] = field(default_factory=lambda: deque(maxlen=MAX_LATENCY_SAMPLES))
    seen_seq: dict[tuple[str, int], None] = field(default_factory=dict)
    burst_events: deque[tuple[int, int]] = field(default_factory=deque)
    burst_peak: int = 0
    token_stats: dict[str, TokenStat] = field(default_factory=dict)
    dup_set_resets: int = 0
    subscribe_errors: list[str] = field(default_factory=list)
    forced_drop_wall: list[float] = field(default_factory=list)
    sample_event: dict | None = None

    def open_csv(self) -> None:
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        self.csv_file = self.csv_path.open("w", newline="", encoding="utf-8")
        self.csv_writer = csv.DictWriter(self.csv_file, fieldnames=CSV_FIELDS)
        self.csv_writer.writeheader()
        self.csv_file.flush()

    def close_csv(self) -> None:
        with self.lock:
            self._flush_current_locked()
            if self.csv_file is not None:
                self.csv_file.close()
                self.csv_file = None
                self.csv_writer = None

    def on_connect(self) -> None:
        with self.lock:
            self.connect_count += 1
            if self.last_disconnect_mono_ns is not None:
                now = time.monotonic_ns()
                self.disconnect_duration_s += (now - self.last_disconnect_mono_ns) / 1e9
                self.last_disconnect_mono_ns = None

    def on_disconnect(self) -> None:
        with self.lock:
            self.disconnect_count += 1
            self.last_disconnect_mono_ns = time.monotonic_ns()

    def on_error(self) -> None:
        with self.lock:
            self.error_count += 1

    def on_heartbeat(self) -> None:
        with self.lock:
            self.total_heartbeats += 1

    def note_subscribe_error(self, text: str) -> None:
        with self.lock:
            self.subscribe_errors.append(text[:500])

    def note_forced_drop(self) -> None:
        with self.lock:
            self.forced_drop_wall.append(time.time())

    def on_frame(
        self,
        *,
        receive_wall: float,
        receive_mono_ns: int,
        wire_bytes: int,
        parsed: dict | None,
        parse_error: bool,
    ) -> None:
        with self.lock:
            bucket_s = int(receive_wall)
            self._roll_bucket_locked(bucket_s)

            assert self.current is not None
            self.current.messages += 1
            self.total_messages += 1
            self.current.bytes += wire_bytes
            self.total_bytes += wire_bytes
            if wire_bytes > self.current.max_message_bytes:
                self.current.max_message_bytes = wire_bytes
            if wire_bytes > self.max_message_bytes:
                self.max_message_bytes = wire_bytes

            if self.last_message_mono_ns is not None:
                delta = receive_mono_ns - self.last_message_mono_ns
                if delta >= 0:
                    self.interarrival_ns.append(delta)
            self.last_message_mono_ns = receive_mono_ns

            events = events_from_payload(parsed) if parsed is not None and not parse_error else []
            if parse_error or parsed is None or not events:
                self.current.invalid += 1
                self.total_invalid += 1
                return

            self.current.events += len(events)
            self.total_events += len(events)
            self._update_burst_locked(receive_mono_ns, len(events))

            for event in events:
                if missing_required(event):
                    self.current.invalid += 1
                    self.total_invalid += 1
                    continue
                token = str(event.get("token", ""))
                seq = event.get("sequence_number")
                if seq is not None:
                    try:
                        key = (token, int(seq))
                    except (TypeError, ValueError):
                        key = None
                    if key is not None:
                        if key in self.seen_seq:
                            self.current.duplicates += 1
                            self.total_duplicates += 1
                        else:
                            if len(self.seen_seq) >= MAX_DUP_KEYS:
                                self.seen_seq.clear()
                                self.dup_set_resets += 1
                            self.seen_seq[key] = None
                stat = self.token_stats.get(token)
                if stat is None:
                    stat = TokenStat()
                    self.token_stats[token] = stat
                note_event(stat, event)
                if self.sample_event is None:
                    self.sample_event = dict(event)
                exchange_ts = event.get("exchange_timestamp")
                lag = exchange_to_receive_ms(exchange_ts, receive_wall)
                if lag is not None:
                    self.latency_ms.append(lag)

    def _update_burst_locked(self, receive_mono_ns: int, n_events: int) -> None:
        self.burst_events.append((receive_mono_ns, n_events))
        cutoff = receive_mono_ns - BURST_WINDOW_NS
        while self.burst_events and self.burst_events[0][0] < cutoff:
            self.burst_events.popleft()
        window_sum = sum(n for _, n in self.burst_events)
        if window_sum > self.burst_peak:
            self.burst_peak = window_sum

    def _roll_bucket_locked(self, bucket_s: int) -> None:
        if self.current is None:
            self.current = SecondBucket(start_s=bucket_s)
            return
        if bucket_s == self.current.start_s:
            return
        while self.current.start_s < bucket_s:
            self._write_bucket_locked(self.current)
            self.buckets.append(self.current)
            self.current = SecondBucket(start_s=self.current.start_s + 1)

    def _flush_current_locked(self) -> None:
        if self.current is None:
            return
        self._write_bucket_locked(self.current)
        self.buckets.append(self.current)
        self.current = None

    def _write_bucket_locked(self, bucket: SecondBucket) -> None:
        if self.csv_writer is None or self.csv_file is None:
            return
        ts = datetime.fromtimestamp(bucket.start_s, tz=IST)
        self.csv_writer.writerow(
            {
                "bucket_start_ist": ts.isoformat(),
                "period": session_period(ts),
                "messages": bucket.messages,
                "events": bucket.events,
                "bytes": bucket.bytes,
                "max_message_bytes": bucket.max_message_bytes,
                "invalid": bucket.invalid,
                "duplicates": bucket.duplicates,
            }
        )
        self.csv_file.flush()

    def elapsed_s(self) -> float:
        return max((time.monotonic_ns() - self.started_mono_ns) / 1e9, 0.001)

    def snapshot(self) -> dict:
        with self.lock:
            elapsed = self.elapsed_s()
            ia = sorted(x / 1e6 for x in self.interarrival_ns)
            lag = sorted(self.latency_ms)
            peak_events = max((b.events for b in self.buckets), default=0)
            if self.current is not None:
                peak_events = max(peak_events, self.current.events)
            peak_bytes = max((b.bytes for b in self.buckets), default=0)
            if self.current is not None:
                peak_bytes = max(peak_bytes, self.current.bytes)
            ooo = sum(s.exchange_ts_backwards for s in self.token_stats.values())
            dup_ts = sum(s.duplicate_exchange_ts for s in self.token_stats.values())
            invalid_ltp = sum(s.invalid_ltp for s in self.token_stats.values())
            seq_back = sum(s.seq_backwards for s in self.token_stats.values())
            seq_jumps = sum(s.seq_jumps for s in self.token_stats.values())
            tokens_with_events = sum(1 for s in self.token_stats.values() if s.events)
            avg_bps = self.total_bytes / elapsed if elapsed else 0
            return {
                "elapsed_s": round(elapsed, 3),
                "subscribed": len(self.tokens),
                "tokens_with_events": tokens_with_events,
                "messages": self.total_messages,
                "events": self.total_events,
                "bytes": self.total_bytes,
                "heartbeats": self.total_heartbeats,
                "avg_messages_per_sec": self.total_messages / elapsed,
                "avg_events_per_sec": self.total_events / elapsed,
                "peak_events_per_sec": peak_events,
                "burst_events_100ms": self.burst_peak,
                "avg_message_bytes": (self.total_bytes / self.total_messages) if self.total_messages else 0,
                "max_message_bytes": self.max_message_bytes,
                "avg_bytes_per_sec": avg_bps,
                "peak_bytes_per_sec": peak_bytes,
                "extrapolated_events_per_hour": self.total_events / elapsed * 3600,
                "extrapolated_events_per_day": self.total_events / elapsed * 86400,
                "extrapolated_events_per_month": self.total_events / elapsed * 86400 * 30,
                "extrapolated_gb_per_day": avg_bps * 86400 / (1024 ** 3),
                "interarrival_p50_ms": _percentile(ia, 0.50),
                "interarrival_p95_ms": _percentile(ia, 0.95),
                "interarrival_p99_ms": _percentile(ia, 0.99),
                "exchange_to_receive_p50_ms": _percentile(lag, 0.50),
                "exchange_to_receive_p95_ms": _percentile(lag, 0.95),
                "duplicates": self.total_duplicates,
                "invalid": self.total_invalid,
                "invalid_ltp": invalid_ltp,
                "out_of_order_exchange_ts": ooo,
                "duplicate_exchange_ts": dup_ts,
                "sequence_backwards": seq_back,
                "sequence_jumps": seq_jumps,
                "dup_set_resets": self.dup_set_resets,
                "connects": self.connect_count,
                "disconnects": self.disconnect_count,
                "errors": self.error_count,
                "forced_drops": len(self.forced_drop_wall),
                "subscribe_errors": list(self.subscribe_errors),
            }

    def export_run(self, run_dir: Path, meta: dict) -> dict:
        run_dir.mkdir(parents=True, exist_ok=True)
        self.close_csv()
        snap = self.snapshot()
        snap.update(meta)
        by_token = run_dir / "by_token.csv"
        with by_token.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "token",
                    "events",
                    "seq_backwards",
                    "seq_jumps",
                    "exchange_ts_backwards",
                    "duplicate_exchange_ts",
                    "invalid_ltp",
                ],
            )
            writer.writeheader()
            ranked = sorted(self.token_stats.items(), key=lambda item: item[1].events, reverse=True)
            for token, stat in ranked:
                writer.writerow(
                    {
                        "token": token,
                        "events": stat.events,
                        "seq_backwards": stat.seq_backwards,
                        "seq_jumps": stat.seq_jumps,
                        "exchange_ts_backwards": stat.exchange_ts_backwards,
                        "duplicate_exchange_ts": stat.duplicate_exchange_ts,
                        "invalid_ltp": stat.invalid_ltp,
                    }
                )
        if self.sample_event is not None:
            (run_dir / "sample_event.json").write_text(
                json.dumps(self.sample_event, indent=2, default=str) + "\n",
                encoding="utf-8",
            )
        (run_dir / "summary.json").write_text(
            json.dumps(snap, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
        return snap

    def write_profile(self, path: Path, extra: dict[str, str] | None = None) -> bool:
        extra = extra or {}
        elapsed = self.elapsed_s()
        if self.total_events < MIN_EVENTS_FOR_PROFILE:
            path.write_text(
                "\n".join(
                    [
                        "# SOURCE DATA PROFILE",
                        "",
                        f"Not written: only {self.total_events} events (need {MIN_EVENTS_FOR_PROFILE}).",
                        "Run during NSE cash hours (~09:15–15:30 IST) with a live subscription.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            return False

        ia = sorted(x / 1e6 for x in self.interarrival_ns)
        lag = sorted(self.latency_ms)
        peak_events = max((b.events for b in self.buckets), default=0)
        peak_messages = max((b.messages for b in self.buckets), default=0)
        peak_bytes = max((b.bytes for b in self.buckets), default=0)
        avg_msg = self.total_bytes / self.total_messages if self.total_messages else 0
        avg_events = self.total_events / elapsed
        avg_messages = self.total_messages / elapsed
        avg_bps = self.total_bytes / elapsed
        hours = elapsed / 3600.0
        disconnects_per_hour = self.disconnect_count / hours if hours > 0 else 0.0

        period_counts: dict[str, int] = {}
        for b in self.buckets:
            ts = datetime.fromtimestamp(b.start_s, tz=IST)
            p = session_period(ts)
            period_counts[p] = period_counts.get(p, 0) + b.events
        dominant = max(period_counts, key=period_counts.get) if period_counts else "unknown"

        start_ist = datetime.fromtimestamp(self.started_wall, tz=IST)
        end_ist = datetime.now(IST)

        lines = [
            "# SOURCE DATA PROFILE",
            "",
            "30-minute sample (or until interrupt). Not a full trading session.",
            f"**Period (dominant IST bucket):** {dominant}",
            "",
            "## Source",
            "",
            "- Source: Angel One WebSocket",
            "- Mode: LTP (subscription_mode = 1)",
            f"- Subscriptions: {len(self.tokens)} instruments",
            f"- Tokens: `{', '.join(self.tokens)}`",
            f"- Window start (IST): {start_ist.isoformat()}",
            f"- Window end (IST): {end_ist.isoformat()}",
            f"- Elapsed seconds: {_fmt(elapsed, 1)}",
            "",
            "## Rates",
            "",
            f"- Average messages/sec: {_fmt(avg_messages)}",
            f"- Peak messages/sec (1s bucket): {peak_messages}",
            f"- Average events/sec: {_fmt(avg_events)}",
            f"- Peak events/sec (1s bucket): {peak_events}",
            f"- Burst peak (events in 100ms): {self.burst_peak}",
            f"- Messages/minute (average): {_fmt(avg_messages * 60, 1)}",
            "",
            "Message vs event: events are counted from the parsed payload, not assumed equal to frames.",
            f"- Total messages (data frames): {self.total_messages}",
            f"- Total events: {self.total_events}",
            f"- Heartbeats (ping/pong, excluded from data): {self.total_heartbeats}",
            "",
            "## Volume",
            "",
            f"- Average message size: {_fmt(avg_msg, 1)} bytes",
            f"- Peak message size: {self.max_message_bytes} bytes",
            f"- Average bandwidth: {_fmt(avg_bps / 1024, 3)} KB/sec",
            f"- Peak bandwidth (1s): {_fmt(peak_bytes / 1024, 3)} KB/sec",
            f"- Extrapolated MB/min: {_fmt(avg_bps * 60 / (1024 * 1024), 4)} (from this window only)",
            f"- Extrapolated GB/hour: {_fmt(avg_bps * 3600 / (1024 ** 3), 4)} (from this window only)",
            "",
            "## Timing",
            "",
            f"- Inter-arrival p50 (messages): {_fmt(_percentile(ia, 0.50))} ms",
            f"- Inter-arrival p95: {_fmt(_percentile(ia, 0.95))} ms",
            f"- Inter-arrival p99: {_fmt(_percentile(ia, 0.99))} ms",
            f"- Average inter-arrival: {_fmt(sum(ia) / len(ia) if ia else None)} ms",
            "",
            f"- exchange_to_receive p50: {_fmt(_percentile(lag, 0.50))} ms",
            f"- exchange_to_receive p95: {_fmt(_percentile(lag, 0.95))} ms",
            "",
            "Caveat: `exchange_to_receive` is `receive_ts - exchange_timestamp`. NSE and this machine are not a shared clock. Do not treat this as network RTT.",
            "",
            "## Quality / connection",
            "",
            f"- Connects: {self.connect_count}",
            f"- Disconnects: {self.disconnect_count}",
            f"- Disconnects/hour (extrapolated): {_fmt(disconnects_per_hour, 2)}",
            f"- Time spent disconnected (s): {_fmt(self.disconnect_duration_s, 2)}",
            f"- Callback errors: {self.error_count}",
            f"- Duplicate (token, sequence_number): {self.total_duplicates}",
            f"- Malformed / invalid: {self.total_invalid}",
            "",
            "## Period mix (events per IST bucket in this window)",
            "",
        ]
        for name, count in sorted(period_counts.items()):
            lines.append(f"- {name}: {count}")
        lines.extend(
            [
                "",
                "## NFR targets",
                "",
                "Not set from assumptions. Size the next hop from this peak × headroom after more windows (especially 09:15 IST open).",
                "",
                f"- Observed peak events/sec: {peak_events}",
                "- Suggested next step: compare a 30-minute run at market open before locking NFRs.",
                "",
            ]
        )
        for key, value in extra.items():
            lines.append(f"- {key}: {value}")
        lines.append("")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        return True


def events_from_payload(parsed: dict) -> list[dict]:
    nested = parsed.get("events") or parsed.get("ticks") or parsed.get("data")
    if isinstance(nested, list) and nested and all(isinstance(x, dict) for x in nested):
        return nested
    return [parsed]


def missing_required(event: dict) -> bool:
    return event.get("token") in (None, "") or event.get("last_traded_price") is None


def exchange_to_receive_ms(exchange_ts: object, receive_wall: float) -> float | None:
    if exchange_ts is None:
        return None
    try:
        raw = float(exchange_ts)
    except (TypeError, ValueError):
        return None
    if raw > 1e12:
        exchange_s = raw / 1000.0
    elif raw > 1e9:
        exchange_s = raw
    else:
        return None
    return (receive_wall - exchange_s) * 1000.0
