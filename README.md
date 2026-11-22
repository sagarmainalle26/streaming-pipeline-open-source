# Streaming pipeline

Market-data streaming pipeline for NSE (Angel One SmartAPI). Each proof of concept lives under `poc/` and runs on its own.

| POC | What it answers |
|---|---|
| [poc/source-initial](poc/source-initial/README.md) | Section 3.4: what the Angel One websocket sends, event rate at 5 / 50 / 500 / 1,000 instruments, bursts, data quality, and connection behavior |

## Credentials

Copy `.env.example` to `.env` at the repo root and fill in the Angel One values. `.env` is ignored by git; do not commit it or paste its values into code, docs, or results.
