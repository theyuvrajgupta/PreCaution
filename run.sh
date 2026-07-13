#!/usr/bin/env bash
# One-command local run for preCaution (macOS / Linux).
#
# Creates a virtual environment, installs dependencies, makes sure a .env
# exists, then launches the web app. Safe to run repeatedly: it only does the
# setup work that is actually missing, and works offline once set up.
#
#   bash run.sh
#
set -euo pipefail
cd "$(dirname "$0")"

# 1. Find a Python interpreter (3.10+ works; 3.13 is what this was built on).
PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
  echo "ERROR: No Python interpreter found." >&2
  echo "       Install Python 3.10+ (3.13 recommended) from https://www.python.org/downloads/" >&2
  exit 1
fi

# 2. Create the virtual environment on first run.
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment (.venv)..."
  "$PY" -m venv .venv
fi
VENV_PY=".venv/bin/python"

# 3. Install dependencies only when they are missing (first run, or a fresh
#    machine). Skipping this on later runs keeps startup fast and offline-safe.
if ! "$VENV_PY" -c "import uvicorn, fastapi, anthropic" >/dev/null 2>&1; then
  echo "Installing dependencies..."
  "$VENV_PY" -m pip install -r requirements.txt
fi

# 4. Make sure a .env exists so the API key can be read.
if [ ! -f ".env" ]; then
  cp .env.example .env
  echo "Created .env from .env.example."
fi

# 5. Warn (do not block) if the key is still blank. The app boots either way;
#    only reading a protocol needs the key.
if ! grep -qE '^ANTHROPIC_API_KEY=.+' .env; then
  echo ""
  echo "  WARNING: ANTHROPIC_API_KEY is not set in .env."
  echo "  The app will start, but reading a protocol needs a key."
  echo "  Get one at https://console.anthropic.com/ and add it to .env."
  echo ""
fi

# 6. Launch.
echo ""
echo "  preCaution is starting at  http://127.0.0.1:8000"
echo "  Press Ctrl+C to stop."
echo ""
exec "$VENV_PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
