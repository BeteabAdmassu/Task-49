#!/bin/bash
set -euo pipefail

# ── Guard: must run from repo root (where app/ lives) ──────────────────────
if [[ ! -d "app" || ! -d "unit_tests" || ! -d "API_tests" ]]; then
  echo "[run_tests] ERROR: Run this script from the repo root (expected app/, unit_tests/, API_tests/)" >&2
  exit 1
fi

# ── Guard: Python must be available ────────────────────────────────────────
if ! python -c "import flask, pytest" 2>/dev/null; then
  echo "[run_tests] ERROR: Missing dependencies. Run: pip install -r requirements.txt" >&2
  exit 1
fi

# ── Guard: Playwright required in CI ───────────────────────────────────────
if [[ "${CI:-}" == "1" ]]; then
  if ! python -c "from playwright.sync_api import sync_playwright" 2>/dev/null; then
    echo "[run_tests] ERROR: Playwright is required in CI. Run: pip install playwright && playwright install chromium" >&2
    exit 1
  fi
fi

# ── Stable temp/cache paths ────────────────────────────────────────────────
mkdir -p .pytest_tmp .pytest_runtime/cache

run_id="run_$(date +%s)_$$"
base_tmp=".pytest_runtime/tmp/${run_id}"
mkdir -p "$base_tmp"

# ── Run tests ──────────────────────────────────────────────────────────────
python -m pytest unit_tests API_tests \
  --basetemp "$base_tmp" \
  --tb=short \
  "$@"
