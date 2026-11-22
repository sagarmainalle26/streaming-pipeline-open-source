# 3.4 Source Data Analysis & Initial POC

Numbers below come from `results/runs/`. Scale figures use the clean `scale-*-ticks` runs (10 minutes each, one connection, every tick saved under `results/raw/`).
Hour, day, and month figures are extrapolations of the measured window.
`exchange_to_receive` compares the exchange clock with this machine and is not network RTT.
Angel `sequence_number` is not a dense counter for this subscription, so a jump is not counted as a lost event.

## 3.4.1 Source Overview

- Source: Angel One SmartAPI WebSocket V2 (`wss://smartapisocket.angelone.in/smart-stream`).
- Feed type: binary market-data frames. One parsed update is counted as one event; frames and events are counted separately.
- Subscription mode for rates: LTP (mode 1). Quote (mode 2) is used only to list extra fields.
- Instruments: NSE cash equities. The 5-name set is RELIANCE, TCS, HDFCBANK, BHARTIARTL, ICICIBANK. Larger sets are prefixes plus further `-EQ` names (`config/tokens/nse_50.txt`, `nse_500.txt`, `nse_1000.txt`).
- Market information on LTP: instrument token, exchange type, last traded price, exchange timestamp, sequence.
- Quote adds last quantity, average price, day volume, buy/sell quantity, and day OHLC.

## 3.4.2 Data Structure

```text
Event
 ├── token                  instrument id
 ├── exchange_type          NSE cash = 1
 ├── last_traded_price      paise
 ├── exchange_timestamp     epoch ms
 ├── sequence_number
 └── quote-only fields      quantity, volume, day OHLC
```

LTP fields:

- `subscription_mode` (int, mandatory, seen): 1 = LTP, 2 = Quote, 3 = SnapQuote
- `subscription_mode_val` (string, optional, seen): Label for subscription_mode
- `exchange_type` (int, mandatory, seen): 1 = NSE cash
- `token` (string, mandatory, seen): Instrument id, not the ticker
- `sequence_number` (int, mandatory on LTP, seen): Feed sequence. Not a dense per-subscription counter.
- `exchange_timestamp` (int, mandatory, seen): Exchange time, epoch milliseconds
- `last_traded_price` (int, mandatory, seen): Price in paise. Rupees = value / 100

Quote fields beyond LTP:

- `last_traded_quantity` (int, quote/snap, seen): Last traded quantity
- `average_traded_price` (int, quote/snap, seen): Paise
- `volume_trade_for_the_day` (int, quote/snap, seen): Day volume
- `total_buy_quantity` (float, quote/snap, seen): Outstanding buy quantity
- `total_sell_quantity` (float, quote/snap, seen): Outstanding sell quantity
- `open_price_of_the_day` (int, quote/snap, seen): Paise
- `high_price_of_the_day` (int, quote/snap, seen): Paise
- `low_price_of_the_day` (int, quote/snap, seen): Paise
- `closed_price` (int, quote/snap, seen): Previous close, paise

Sample LTP raw value `209100` is 2091.00 rupees after dividing by 100.

## 3.4.3 Data Arrival Characteristics

- Structure LTP (5 names, ~3 min): avg 7.89 events/sec, peak 1s 20, burst 100ms 10, window 2026-10-06T12:15:35.475122+05:30 to 2026-10-06T12:18:37.206789+05:30 (normal)
- Scale 5 names: avg 8.57 events/sec, peak 1s 24, burst 100ms 13, window 2026-10-06T13:58:43.803032+05:30 to 2026-10-06T14:08:44.203979+05:30 (normal)
- Scale 50 names: avg 55.13 events/sec, peak 1s 163, burst 100ms 99, window 2026-10-06T14:08:49.886298+05:30 to 2026-10-06T14:18:51.785730+05:30 (normal)
- Scale 500 names: avg 147.51 events/sec, peak 1s 766, burst 100ms 504, window 2026-10-06T14:18:57.401460+05:30 to 2026-10-06T14:28:57.828134+05:30 (normal)
- Scale 1000 names: avg 246.04 events/sec, peak 1s 1033, burst 100ms 879, window 2026-10-06T13:47:54.053914+05:30 to 2026-10-06T13:57:54.459301+05:30 (normal)

Per-instrument share (highest counts in the scale-50 run, else scale-5):

- token 1964: 1327 events
- token 3045: 1310 events
- token 10604: 1267 events
- token 11483: 1246 events
- token 2885: 1243 events

## 3.4.4 Data Volume

Reference: largest clean scale run (1000 names). Day and month scale the measured average to one 09:15–15:30 session (22,500 s) and 22 trading days. Open and close run hotter than this window, so treat these as a floor.

- bytes/event (average frame): 51.0
- events/sec (average): 246.04
- events/hour (extrapolated): 885,757
- events/trading day (extrapolated): 5,535,980
- events/month (extrapolated, 22 trading days): 121,791,552
- GB/trading day (extrapolated, wire bytes): 0.263

Use the scale table below before treating one window as the storage workload. A 5-name window is not a 1,000-name day.

## 3.4.5 Instrument Scaling

- 5 requested, 5 with at least one event: avg 8.57 events/sec, peak 24/sec, avg message 51.0 bytes, subscribe errors 0
- 50 requested, 50 with at least one event: avg 55.13 events/sec, peak 163/sec, avg message 51.0 bytes, subscribe errors 0
- 500 requested, 500 with at least one event: avg 147.51 events/sec, peak 766/sec, avg message 51.0 bytes, subscribe errors 0
- 1000 requested, 1000 with at least one event: avg 246.04 events/sec, peak 1033/sec, avg message 51.0 bytes, subscribe errors 0

Requested sizes were 5, 50, 500, and 1,000 on one connection, LTP only. Tokens with zero events can be quiet names rather than a rejected subscribe. A server limit shows up in `subscribe_errors`.

## 3.4.6 Burst Behavior

- Market open (50 names): not captured
- Normal-hours scale-50: avg 55.13 events/sec, peak 1s 163, burst 100ms 99, window 2026-10-06T14:08:49.886298+05:30 to 2026-10-06T14:18:51.785730+05:30 (normal)
- Market close (50 names, 15:00–15:15 target): avg 46.36 events/sec, peak 1s 215, burst 100ms 92, window 2026-10-06T15:00:09.015631+05:30 to 2026-10-06T15:15:09.431769+05:30 (close)

Open is captured only when a run is started inside 09:15–09:30 IST. This document does not invent that peak.

## 3.4.7 Data Quality

Counts are summed across the four clean scale runs. Latency is from the largest run.

- Duplicate `(token, sequence_number)`: 0
- Malformed or missing required fields: 0
- Non-positive last price: 0
- Exchange timestamp went backwards on a token: 1
- Duplicate exchange timestamp on a token: 69367
- Sequence moved backwards: 0
- Sequence jumped by more than 1: 273066 (not counted as loss)
- Duplicate tracker resets: 0
- exchange_to_receive p50/p95 ms: 398.71 / 987.15

## 3.4.8 Connection Behavior

- Reconnect probe: drop after 120s, connects 2, disconnects 2, forced drops 1, callback errors 0.
- Events in that window: 15844. Sequence jumps: 15793.
- This probe ran before the reconnect fix, so the SDK re-subscribed the 50 names twice (100 subscriptions) after the drop. The capture now replaces the list on reconnect.
- The four scale runs held one connection for the full 10 minutes with no drop.
- The websocket does not replay missed ticks. A hole across the drop is data loss for this consumer unless a later durable log captured the raw feed.

## 3.4.9 Source Limitations

- Up to 3 websocket connections per client (Angel One account rule).
- Documented quota is 1,000 token subscriptions per connection. LTP, Quote, and SnapQuote on the same token count as three subscriptions.
- Depth mode is capped at 50 tokens in the SDK and was not used.
- Login needs API key, client code, PIN, and TOTP. The JWT and feed token expire; a long study logs in again per run.
- This websocket has no tick replay. Historical candles are a separate REST API.
- Market data use is bound by the Angel One SmartAPI terms for the account that owns the key.

## 3.4.10 Source POC Results

Clean 10-minute LTP runs, normal session. Daily volume scales the window average to one 09:15–15:30 session; it is not a measured open-to-close total.

| Characteristic | 5 | 50 | 500 | 1,000 | Implication |
|---|---:|---:|---:|---:|---|
| Instruments with events | 5 | 50 | 500 | 1000 | All subscribed names sent at least one tick |
| Average event rate | 8.6/sec | 55.1/sec | 147.5/sec | 246.0/sec | Ingestion rate grows with the watchlist, not linearly |
| Peak 1-sec rate | 24/sec | 163/sec | 766/sec | 1033/sec | Size the pipeline for the peak, not the average |
| Burst (100 ms) | 13 events | 99 events | 504 events | 879 events | Short spikes are several times the average second |
| Event size | 51 bytes | 51 bytes | 51 bytes | 51 bytes | Frame size does not change with watchlist size |
| Volume per trading day (extrapolated) | 0.2M events, 0.009 GB | 1.2M events, 0.059 GB | 3.3M events, 0.158 GB | 5.5M events, 0.263 GB | Storage scales with name count |
| Missing events | Not proven | Not proven | Not proven | Not proven | Sequence jumps on almost every tick, so a gap is not evidence of loss |
| Duplicate events | 0 | 0 | 0 | 0 | No `(token, sequence)` dedup needed on these runs |
| Out-of-order events | 0 | 0 | 1 | 0 | Exchange time stayed in order apart from isolated ticks |
| Reconnection | Held 10 min | Held 10 min | Held 10 min | Held 10 min | Scale runs did not drop; the feed does not replay missed ticks |

Highest 1-second rate observed in any clean window: 1033 events/sec.

NFR: size the next hop from the observed peak above, with headroom, after an open-auction run exists. Do not use a 10,000 or 25,000 events/sec assumption.
