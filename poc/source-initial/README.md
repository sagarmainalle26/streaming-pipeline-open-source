# Source initial POC (Angel One → metrics)

Section 3.4 of the design: what the Angel One SmartAPI websocket actually sends, and how much of it. No Kafka, Redis, Flink, or ClickHouse.

The write-up is [docs/source-data-analysis.md](docs/source-data-analysis.md). Every number in it comes from `results/runs/`.

## Layout

```text
poc/source-initial/
├── src/source_capture/   capture app, metrics, token universe, study runner, report
├── tests/                unit tests for the metric helpers
├── config/tokens/        NSE token lists: nse_5, nse_50, nse_500, nse_1000
├── docs/                 source-data-analysis.md (3.4.1–3.4.10)
└── results/
    ├── runs/<label>_<time>/   summary.json, by_token.csv, metrics_1s.csv, sample_event.json
    └── raw/<label>_<time>/    ticks.jsonl (every tick; not committed)
```

## Setup

Credentials come from a `.env` at the repo root (or in this folder; this folder's file wins). Copy `.env.example` from the repo root and fill:

- `ANGEL_API_KEY`
- `ANGEL_CLIENT_CODE`
- `ANGEL_PIN`
- `ANGEL_TOTP_SECRET` (or `ANGEL_TOTP` for a one-shot code)

Never commit `.env`. It is in `.gitignore`.

Python 3.9+:

```bash
cd poc/source-initial
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export PYTHONPATH=src
```

Angel allows three websocket connections per client and about 1,000 token subscriptions per connection. Stop other ticker apps before a run. Cash session is about 09:15–15:30 IST; off-hours runs produce no usable data. Keep the machine awake during a run (`caffeinate -i` on macOS); a sleeping laptop leaves silent gaps in the data.

## Run

One capture (10 minutes, 50 names, every tick saved):

```bash
python -m source_capture --label scale-50-ticks --tokens-file config/tokens/nse_50.txt --duration 600 --dump-ticks
```

Options: `--mode 1|2|3` (LTP, Quote, SnapQuote), `--drop-after N` (force a socket drop to test reconnect).

Rebuild the token lists from Angel's scrip master (the 33 MB master is cached under `config/tokens/` and not committed):

```bash
python -m source_capture.universe
```

Full study (structure, 5/50/500/1000, reconnect, and the 15:00 IST close window), then regenerate the doc:

```bash
python -m source_capture.study
```

Regenerate only the doc from existing runs:

```bash
python -m source_capture.report
```

Market open (09:15–09:30 IST) is a separate run:

```bash
python -m source_capture --label burst-open --tokens-file config/tokens/nse_50.txt --duration 900 --dump-ticks
```

## What is measured

| Metric | Notes |
|---|---|
| messages/sec | Binary data frames, not ping/pong |
| events/sec | Parsed updates inside each frame |
| bytes/sec | Wire size of the binary frame |
| inter-arrival | Time between messages on this machine |
| exchange_to_receive | `receive_ts - exchange_timestamp`; not network RTT (different clocks) |
| duplicates | Same `(token, sequence_number)` twice |
| out-of-order | Exchange timestamp went backwards on one token |
| invalid | Parse failure, missing token or price, price ≤ 0 |

`last_traded_price` is in paise. Divide by 100 for rupees.

## Data handling

`results/raw/` holds Angel One market data captured under one account. It is excluded from git for size and because redistribution is governed by the SmartAPI terms. The committed files are aggregate counts (`summary.json`, `by_token.csv`, `metrics_1s.csv`) and the doc. Each run's `sample_event.json` (one raw tick) is also excluded from git.
