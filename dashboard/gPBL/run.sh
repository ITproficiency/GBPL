#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$ROOT/backend"
cd "$BACKEND"

echo ""
echo "PostureCare gPBL — local demo"
echo ""

command -v python3 >/dev/null || { echo "Python 3 not found."; exit 1; }

echo ">> Creating virtualenv if needed..."
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m ensurepip --upgrade >/dev/null 2>&1 || true

echo ">> Installing dependencies..."
pip install -q --upgrade pip
pip install -q -r requirements.txt

echo ">> Checking config files..."
[[ -f .env ]] || cp .env.example .env
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
