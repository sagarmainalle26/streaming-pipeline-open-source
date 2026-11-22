"""Build NSE equity token files from the Angel One scrip master."""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from source_capture.paths import POC_ROOT, TOKENS_DIR

SCRIP_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
CACHE = TOKENS_DIR / "OpenAPIScripMaster.json"

# Liquid names first so smaller files are prefixes of larger ones.
PRIORITY = [
    "RELIANCE",
    "TCS",
    "HDFCBANK",
    "BHARTIARTL",
    "ICICIBANK",
    "INFY",
    "SBIN",
    "HINDUNILVR",
    "ITC",
    "LT",
    "BAJFINANCE",
    "HCLTECH",
    "MARUTI",
    "SUNPHARMA",
    "KOTAKBANK",
    "AXISBANK",
    "TITAN",
    "ULTRACEMCO",
    "NTPC",
    "ASIANPAINT",
    "M&M",
    "WIPRO",
    "POWERGRID",
    "TATAMOTORS",
    "ADANIENT",
    "ADANIPORTS",
    "JSWSTEEL",
    "TATASTEEL",
    "NESTLEIND",
    "BAJAJFINSV",
    "TECHM",
    "HINDALCO",
    "GRASIM",
    "CIPLA",
    "ONGC",
    "COALINDIA",
    "DRREDDY",
    "BRITANNIA",
    "EICHERMOT",
    "APOLLOHOSP",
    "DIVISLAB",
    "HEROMOTOCO",
    "BPCL",
    "INDUSINDBK",
    "SHRIRAMFIN",
    "TRENT",
    "BEL",
    "JIOFIN",
    "HDFCLIFE",
    "SBILIFE",
]

SIZES = (5, 50, 500, 1000)


def fetch_scrip(path: Path = CACHE) -> list[dict]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        urllib.request.urlretrieve(SCRIP_URL, path)
    return json.loads(path.read_text(encoding="utf-8"))


def nse_eq_tokens(rows: list[dict]) -> dict[str, str]:
    found: dict[str, str] = {}
    for row in rows:
        if row.get("exch_seg") != "NSE":
            continue
        symbol = str(row.get("symbol") or "")
        if not symbol.endswith("-EQ"):
            continue
        token = str(row.get("token") or "").strip()
        if not token:
            continue
        found[symbol[: -len("-EQ")]] = token
    return found


def ordered_names(mapping: dict[str, str]) -> list[tuple[str, str]]:
    ordered: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name in PRIORITY:
        token = mapping.get(name)
        if token and token not in seen:
            ordered.append((token, name))
            seen.add(token)
    for name in sorted(mapping):
        token = mapping[name]
        if token not in seen:
            ordered.append((token, name))
            seen.add(token)
    return ordered


def write_sets(rows: list[dict], out_dir: Path | None = None) -> dict[int, int]:
    out_dir = out_dir or TOKENS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ordered = ordered_names(nse_eq_tokens(rows))
    written: dict[int, int] = {}
    for size in SIZES:
        subset = ordered[:size]
        path = out_dir / f"nse_{size}.txt"
        lines = [f"{token} {name}" for token, name in subset]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written[size] = len(subset)
    return written


def main() -> None:
    rows = fetch_scrip()
    counts = write_sets(rows)
    for size, count in counts.items():
        print(f"{(TOKENS_DIR / f'nse_{size}.txt').relative_to(POC_ROOT)} {count}")


if __name__ == "__main__":
    main()
