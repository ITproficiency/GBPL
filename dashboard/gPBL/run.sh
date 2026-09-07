#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
cd "$BACKEND"

echo ""
echo "PostureCare gPBL — local demo"
echo ""

# Prefer 3.12 → 3.11 → 3.10. Allow 3.13 if nothing else exists (no MediaPipe here).
resolve_python_soft() {
  local cmd ver
  for cmd in python3.12 python3.11 python3.10; do
    if command -v "$cmd" >/dev/null 2>&1; then
      printf '%s' "$cmd"
      return 0
    fi
  done
  for cmd in python3.13 python3; do
    if command -v "$cmd" >/dev/null 2>&1; then
      ver="$("$cmd" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
      case "$ver" in
        3.1[0-9]|3.[2-9][0-9])
          printf '%s' "$cmd"
          return 0
          ;;
      esac
      echo "Python $ver is too old. Dashboard needs Python 3.10+ (3.13 allowed here)." >&2
      echo "Install Python 3.11 (recommended):" >&2
      echo "  macOS:  brew install python@3.11" >&2
      echo "  Linux:  sudo apt install python3.11 python3.11-venv" >&2
      exit 1
    fi
  done
  echo "Python 3.10+ not found. Install Python 3.11 (recommended)." >&2
  echo "  macOS:  brew install python@3.11" >&2
  echo "  Linux:  sudo apt install python3.11 python3.11-venv" >&2
  exit 1
}

PYTHON_CMD="$(resolve_python_soft)"

echo ">> Creating virtualenv if needed..."
if [[ -d .venv && ! -f .venv/pyvenv.cfg ]]; then
  echo "   Broken venv — recreating once" >&2
  rm -rf .venv
fi
if [[ ! -d .venv ]]; then
  "$PYTHON_CMD" -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m ensurepip --upgrade >/dev/null 2>&1 || true

echo ">> Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo ">> Checking config files..."
if [[ ! -f .env.example ]]; then
  echo "MISSING: $BACKEND/.env.example" >&2
  exit 1
fi
[[ -f .env ]] || cp .env.example .env

if [[ ! -f data/rules.json.example ]]; then
  echo "MISSING: $BACKEND/data/rules.json.example" >&2
  exit 1
fi
mkdir -p data
[[ -f data/rules.json ]] || cp data/rules.json.example data/rules.json

if [[ ! -f serviceAccountKey.json ]]; then
  echo ""
  echo "MISSING: backend/serviceAccountKey.json"
  echo "  Firebase Console -> Service accounts -> Generate key"
  echo "  Or: cp serviceAccountKey.json.example serviceAccountKey.json"
  exit 1
fi

if grep -q "OPENROUTER_API_KEY=sk-or-v1-your-key" .env 2>/dev/null; then
  echo "   Note: OPENROUTER_API_KEY default — LLM uses mock until .env is edited"
fi

echo ""
echo "Dashboard: http://localhost:8080/dashboard"
echo ""

exec uvicorn main:app --host 0.0.0.0 --port 8080
