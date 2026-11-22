from __future__ import annotations

from pathlib import Path

POC_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = POC_ROOT.parents[1]

SRC_DIR = POC_ROOT / "src"
TOKENS_DIR = POC_ROOT / "config" / "tokens"
DOCS_DIR = POC_ROOT / "docs"
RUNS_DIR = POC_ROOT / "results" / "runs"
RAW_DIR = POC_ROOT / "results" / "raw"

# The POC's own .env wins; the repo-root .env is the fallback.
ENV_FILES = (POC_ROOT / ".env", REPO_ROOT / ".env")
