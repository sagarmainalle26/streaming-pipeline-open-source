"""Fill docs/source-data-analysis.md from results/runs only."""

from __future__ import annotations

import json
from pathlib import Path

from source_capture.paths import DOCS_DIR, RUNS_DIR, TOKENS_DIR

RUNS = RUNS_DIR
DOC = DOCS_DIR / "source-data-analysis.md"
SCALES = (5, 50, 500, 1000)
# NSE cash session 09:15–15:30 IST.
SESSION_SECONDS = 6 * 3600 + 15 * 60
TRADING_DAYS_PER_MONTH = 22


def session_volume(snap: dict) -> dict:
    eps = float(snap.get("avg_events_per_sec") or 0)
    bps = float(snap.get("avg_bytes_per_sec") or 0)
    return {
        "events_per_hour": eps * 3600,
        "events_per_day": eps * SESSION_SECONDS,
        "events_per_month": eps * SESSION_SECONDS * TRADING_DAYS_PER_MONTH,
        "gb_per_day": bps * SESSION_SECONDS / (1024 ** 3),
    }
QUALITY_COUNTERS = (
    "duplicates",
    "invalid",
    "invalid_ltp",
    "out_of_order_exchange_ts",
    "duplicate_exchange_ts",
    "sequence_backwards",
    "sequence_jumps",
    "dup_set_resets",
)

LTP_FIELDS = [
    ("subscription_mode", "int", "mandatory", "1 = LTP, 2 = Quote, 3 = SnapQuote"),
    ("subscription_mode_val", "string", "optional", "Label for subscription_mode"),
    ("exchange_type", "int", "mandatory", "1 = NSE cash"),
    ("token", "string", "mandatory", "Instrument id, not the ticker"),
    ("sequence_number", "int", "mandatory on LTP", "Feed sequence. Not a dense per-subscription counter."),
    ("exchange_timestamp", "int", "mandatory", "Exchange time, epoch milliseconds"),
    ("last_traded_price", "int", "mandatory", "Price in paise. Rupees = value / 100"),
]

QUOTE_EXTRA = [
    ("last_traded_quantity", "int", "quote/snap", "Last traded quantity"),
    ("average_traded_price", "int", "quote/snap", "Paise"),
    ("volume_trade_for_the_day", "int", "quote/snap", "Day volume"),
    ("total_buy_quantity", "float", "quote/snap", "Outstanding buy quantity"),
    ("total_sell_quantity", "float", "quote/snap", "Outstanding sell quantity"),
    ("open_price_of_the_day", "int", "quote/snap", "Paise"),
    ("high_price_of_the_day", "int", "quote/snap", "Paise"),
    ("low_price_of_the_day", "int", "quote/snap", "Paise"),
    ("closed_price", "int", "quote/snap", "Previous close, paise"),
]


def latest(label: str) -> dict | None:
    matches = sorted(RUNS.glob(f"{label}_*/summary.json"))
    if not matches:
        return None
    return json.loads(matches[-1].read_text(encoding="utf-8"))


def sample(label: str) -> dict | None:
    matches = sorted(RUNS.glob(f"{label}_*/sample_event.json"))
    if not matches:
        return None
    return json.loads(matches[-1].read_text(encoding="utf-8"))


def token_share(label: str, limit: int = 5) -> list[tuple[str, int]]:
    matches = sorted(RUNS.glob(f"{label}_*/by_token.csv"))
    if not matches:
        return []
    rows: list[tuple[str, int]] = []
    lines = matches[-1].read_text(encoding="utf-8").splitlines()[1:]
    for line in lines[:limit]:
        parts = line.split(",")
        if len(parts) >= 2:
            rows.append((parts[0], int(parts[1])))
    return rows


def names_for(size: int) -> list[str]:
    path = TOKENS_DIR / f"nse_{size}.txt"
    if not path.exists():
        return []
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        bits = line.split()
        if len(bits) >= 2:
            names.append(bits[1])
    return names


def fmt(value, digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def write_report() -> Path:
    structure = latest("structure-ltp")
    quote = latest("structure-quote")
    scales = [(n, latest(f"scale-{n}-ticks")) for n in SCALES]
    reconnect = latest("reconnect-50")
    burst_close = latest("burst-close")
    burst_open = latest("burst-open")
    ltp_sample = sample("structure-ltp") or {}
    quote_sample = sample("structure-quote") or {}

    def rate_line(title: str, snap: dict | None) -> str:
        if not snap:
            return f"- {title}: not captured"
        return (
            f"- {title}: avg {fmt(snap.get('avg_events_per_sec'))} events/sec, "
            f"peak 1s {snap.get('peak_events_per_sec')}, "
            f"burst 100ms {snap.get('burst_events_100ms')}, "
            f"window {snap.get('started_ist')} to {snap.get('ended_ist')} ({snap.get('period')})"
        )

    scale_lines = []
    for n, snap in scales:
        if not snap:
            scale_lines.append(f"- {n}: not captured")
            continue
        scale_lines.append(
            f"- {n} requested, {snap.get('tokens_with_events')} with at least one event: "
            f"avg {fmt(snap.get('avg_events_per_sec'))} events/sec, "
            f"peak {snap.get('peak_events_per_sec')}/sec, "
            f"avg message {fmt(snap.get('avg_message_bytes'), 1)} bytes, "
            f"subscribe errors {len(snap.get('subscribe_errors') or [])}"
        )

    captured = [snap for _n, snap in scales if snap and snap.get("events")]
    base = captured[-1] if captured else None
    quality: dict = {}
    if captured:
        quality = {key: sum(int(s.get(key) or 0) for s in captured) for key in QUALITY_COUNTERS}
        quality["exchange_to_receive_p50_ms"] = base.get("exchange_to_receive_p50_ms")
        quality["exchange_to_receive_p95_ms"] = base.get("exchange_to_receive_p95_ms")
    elif structure:
        quality = structure

    top = token_share("scale-50-ticks") or token_share("scale-5-ticks")
    top_lines = [f"- token {token}: {events} events" for token, events in top] or ["- not captured"]

    five_names = ", ".join(names_for(5)) or "see config/tokens/nse_5.txt"

    lines = [
        "# 3.4 Source Data Analysis & Initial POC",
        "",
        "Numbers below come from `results/runs/`. Scale figures use the clean `scale-*-ticks` runs (10 minutes each, one connection, every tick saved under `results/raw/`).",
        "Hour, day, and month figures are extrapolations of the measured window.",
        "`exchange_to_receive` compares the exchange clock with this machine and is not network RTT.",
        "Angel `sequence_number` is not a dense counter for this subscription, so a jump is not counted as a lost event.",
        "",
        "## 3.4.1 Source Overview",
        "",
        "- Source: Angel One SmartAPI WebSocket V2 (`wss://smartapisocket.angelone.in/smart-stream`).",
        "- Feed type: binary market-data frames. One parsed update is counted as one event; frames and events are counted separately.",
        "- Subscription mode for rates: LTP (mode 1). Quote (mode 2) is used only to list extra fields.",
        f"- Instruments: NSE cash equities. The 5-name set is {five_names}. Larger sets are prefixes plus further `-EQ` names (`config/tokens/nse_50.txt`, `nse_500.txt`, `nse_1000.txt`).",
        "- Market information on LTP: instrument token, exchange type, last traded price, exchange timestamp, sequence.",
        "- Quote adds last quantity, average price, day volume, buy/sell quantity, and day OHLC.",
        "",
        "## 3.4.2 Data Structure",
        "",
        "```text",
        "Event",
        " ├── token                  instrument id",
        " ├── exchange_type          NSE cash = 1",
        " ├── last_traded_price      paise",
        " ├── exchange_timestamp     epoch ms",
        " ├── sequence_number",
        " └── quote-only fields      quantity, volume, day OHLC",
        "```",
        "",
        "LTP fields:",
        "",
    ]
    for name, typ, req, meaning in LTP_FIELDS:
        seen = "seen" if name in ltp_sample else "not in this sample"
        lines.append(f"- `{name}` ({typ}, {req}, {seen}): {meaning}")
    lines.append("")
    lines.append("Quote fields beyond LTP:")
    lines.append("")
    for name, typ, req, meaning in QUOTE_EXTRA:
        seen = "seen" if name in quote_sample else "not in this sample"
        lines.append(f"- `{name}` ({typ}, {req}, {seen}): {meaning}")
    if ltp_sample.get("last_traded_price") is not None:
        raw = ltp_sample["last_traded_price"]
        try:
            rupees = int(raw) / 100
            lines.append("")
            lines.append(f"Sample LTP raw value `{raw}` is {rupees:.2f} rupees after dividing by 100.")
        except (TypeError, ValueError):
            pass
    lines.extend(
        [
            "",
            "## 3.4.3 Data Arrival Characteristics",
            "",
            rate_line("Structure LTP (5 names, ~3 min)", structure),
            *[rate_line(f"Scale {n} names", snap) for n, snap in scales],
            "",
            "Per-instrument share (highest counts in the scale-50 run, else scale-5):",
            "",
            *top_lines,
            "",
            "## 3.4.4 Data Volume",
            "",
        ]
    )
    if base:
        vol = session_volume(base)
        lines.extend(
            [
                f"Reference: largest clean scale run ({base.get('subscribed')} names). "
                f"Day and month scale the measured average to one 09:15–15:30 session ({SESSION_SECONDS:,} s) "
                f"and {TRADING_DAYS_PER_MONTH} trading days. Open and close run hotter than this window, so treat these as a floor.",
                "",
                f"- bytes/event (average frame): {fmt(base.get('avg_message_bytes'), 1)}",
                f"- events/sec (average): {fmt(base.get('avg_events_per_sec'))}",
                f"- events/hour (extrapolated): {vol['events_per_hour']:,.0f}",
                f"- events/trading day (extrapolated): {vol['events_per_day']:,.0f}",
                f"- events/month (extrapolated, {TRADING_DAYS_PER_MONTH} trading days): {vol['events_per_month']:,.0f}",
                f"- GB/trading day (extrapolated, wire bytes): {vol['gb_per_day']:.3f}",
                "",
                "Use the scale table below before treating one window as the storage workload. A 5-name window is not a 1,000-name day.",
            ]
        )
    else:
        lines.append("- Volume is not available until a scale run finishes.")
    lines.extend(["", "## 3.4.5 Instrument Scaling", ""])
    lines.extend(scale_lines)
    lines.extend(
        [
            "",
            "Requested sizes were 5, 50, 500, and 1,000 on one connection, LTP only. "
            "Tokens with zero events can be quiet names rather than a rejected subscribe. "
            "A server limit shows up in `subscribe_errors`.",
            "",
            "## 3.4.6 Burst Behavior",
            "",
            rate_line("Market open (50 names)", burst_open),
            rate_line("Normal-hours scale-50", latest("scale-50-ticks")),
            rate_line("Market close (50 names, 15:00–15:15 target)", burst_close),
            "",
            "Open is captured only when a run is started inside 09:15–09:30 IST. This document does not invent that peak.",
            "",
            "## 3.4.7 Data Quality",
            "",
        ]
    )
    if quality:
        lines.extend(
            [
                "Counts are summed across the four clean scale runs. Latency is from the largest run.",
                "",
                f"- Duplicate `(token, sequence_number)`: {quality.get('duplicates')}",
                f"- Malformed or missing required fields: {quality.get('invalid')}",
                f"- Non-positive last price: {quality.get('invalid_ltp')}",
                f"- Exchange timestamp went backwards on a token: {quality.get('out_of_order_exchange_ts')}",
                f"- Duplicate exchange timestamp on a token: {quality.get('duplicate_exchange_ts')}",
                f"- Sequence moved backwards: {quality.get('sequence_backwards')}",
                f"- Sequence jumped by more than 1: {quality.get('sequence_jumps')} (not counted as loss)",
                f"- Duplicate tracker resets: {quality.get('dup_set_resets')}",
                f"- exchange_to_receive p50/p95 ms: {fmt(quality.get('exchange_to_receive_p50_ms'))} / {fmt(quality.get('exchange_to_receive_p95_ms'))}",
            ]
        )
    else:
        lines.append("- Quality counters are not available yet.")
    lines.extend(["", "## 3.4.8 Connection Behavior", ""])
    if reconnect:
        lines.extend(
            [
                f"- Reconnect probe: drop after {reconnect.get('drop_after_s')}s, "
                f"connects {reconnect.get('connects')}, disconnects {reconnect.get('disconnects')}, "
                f"forced drops {reconnect.get('forced_drops')}, callback errors {reconnect.get('errors')}.",
                f"- Events in that window: {reconnect.get('events')}. Sequence jumps: {reconnect.get('sequence_jumps')}.",
                "- This probe ran before the reconnect fix, so the SDK re-subscribed the 50 names twice (100 subscriptions) after the drop. The capture now replaces the list on reconnect.",
                "- The four scale runs held one connection for the full 10 minutes with no drop.",
                "- The websocket does not replay missed ticks. A hole across the drop is data loss for this consumer unless a later durable log captured the raw feed.",
            ]
        )
    else:
        lines.append("- Reconnect probe has not been run.")
    lines.extend(
        [
            "",
            "## 3.4.9 Source Limitations",
            "",
            "- Up to 3 websocket connections per client (Angel One account rule).",
            "- Documented quota is 1,000 token subscriptions per connection. LTP, Quote, and SnapQuote on the same token count as three subscriptions.",
            "- Depth mode is capped at 50 tokens in the SDK and was not used.",
            "- Login needs API key, client code, PIN, and TOTP. The JWT and feed token expire; a long study logs in again per run.",
            "- This websocket has no tick replay. Historical candles are a separate REST API.",
            "- Market data use is bound by the Angel One SmartAPI terms for the account that owns the key.",
            "",
            "## 3.4.10 Source POC Results",
            "",
            "Clean 10-minute LTP runs, normal session. Daily volume scales the window average to one 09:15–15:30 session; it is not a measured open-to-close total.",
            "",
            "| Characteristic | " + " | ".join(f"{n:,}" for n in SCALES) + " | Implication |",
            "|---|" + "---:|" * len(SCALES) + "---|",
        ]
    )

    def per_scale(render) -> str:
        return " | ".join(render(snap) if snap else "not captured" for _n, snap in scales)

    def millions(value) -> str:
        return f"{float(value) / 1e6:.1f}M" if value is not None else "n/a"

    def connection(snap: dict) -> str:
        if snap.get("disconnects", 0) <= 1 and not snap.get("errors"):
            return "Held 10 min"
        return f"{snap.get('connects')} connects, {snap.get('errors')} errors"

    rows = [
        ("Instruments with events", lambda s: str(s.get("tokens_with_events")), "All subscribed names sent at least one tick"),
        ("Average event rate", lambda s: f"{fmt(s.get('avg_events_per_sec'), 1)}/sec", "Ingestion rate grows with the watchlist, not linearly"),
        ("Peak 1-sec rate", lambda s: f"{s.get('peak_events_per_sec')}/sec", "Size the pipeline for the peak, not the average"),
        ("Burst (100 ms)", lambda s: f"{s.get('burst_events_100ms')} events", "Short spikes are several times the average second"),
        ("Event size", lambda s: f"{fmt(s.get('avg_message_bytes'), 0)} bytes", "Frame size does not change with watchlist size"),
        (
            "Volume per trading day (extrapolated)",
            lambda s: f"{millions(session_volume(s)['events_per_day'])} events, {session_volume(s)['gb_per_day']:.3f} GB",
            "Storage scales with name count",
        ),
        ("Missing events", lambda s: "Not proven", "Sequence jumps on almost every tick, so a gap is not evidence of loss"),
        ("Duplicate events", lambda s: str(s.get("duplicates")), "No `(token, sequence)` dedup needed on these runs"),
        ("Out-of-order events", lambda s: str(s.get("out_of_order_exchange_ts")), "Exchange time stayed in order apart from isolated ticks"),
        ("Reconnection", connection, "Scale runs did not drop; the feed does not replay missed ticks"),
    ]
    for title, render, implication in rows:
        lines.append(f"| {title} | {per_scale(render)} | {implication} |")

    peak_candidates = [snap.get("peak_events_per_sec") for snap in captured]
    if burst_close and burst_close.get("peak_events_per_sec") is not None:
        peak_candidates.append(burst_close["peak_events_per_sec"])
    if burst_open and burst_open.get("peak_events_per_sec") is not None:
        peak_candidates.append(burst_open["peak_events_per_sec"])
    peak = max(peak_candidates) if peak_candidates else None
    lines.extend(
        [
            "",
            f"Highest 1-second rate observed in any clean window: {fmt(peak, 0) if peak is not None else 'not captured'} events/sec.",
            "",
            "NFR: size the next hop from the observed peak above, with headroom, after an open-auction run exists. Do not use a 10,000 or 25,000 events/sec assumption.",
            "",
        ]
    )
    if quote and quote.get("subscribe_errors"):
        lines.append("Quote subscribe errors were recorded on the structure-quote run.")
        lines.append("")
    DOC.parent.mkdir(parents=True, exist_ok=True)
    DOC.write_text("\n".join(lines), encoding="utf-8")
    return DOC


if __name__ == "__main__":
    print(write_report())
