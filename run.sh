#!/usr/bin/env bash
# PostureCare — one-run (macOS / Linux): dashboard + tracking AI
# Two venvs, created once, reused after that:
#   dashboard/gPBL/backend/.venv  — FastAPI
#   tracking_AI/.venv             — MediaPipe / OpenCV
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# --- Python 3.10–3.12 resolver (hard: MediaPipe does not support 3.13) ---
resolve_python() {
  local cmd ver
  for cmd in python3.12 python3.11 python3.10; do
    if command -v "$cmd" >/dev/null 2>&1; then
      ver="$("$cmd" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
      case "$ver" in
        3.10|3.11|3.12)
          printf '%s' "$cmd"
          return 0
          ;;
      esac
    fi
  done
  if command -v python3 >/dev/null 2>&1; then
    ver="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    case "$ver" in
      3.10|3.11|3.12)
        printf '%s' "python3"
        return 0
        ;;
    esac
    echo "Python $ver found, but tracking_AI needs Python 3.10–3.12 (MediaPipe)." >&2
    echo "Install Python 3.11 (recommended) or 3.12, e.g.:" >&2
    echo "  macOS:  brew install python@3.11" >&2
    echo "  Linux:  sudo apt install python3.11 python3.11-venv" >&2
    exit 1
  fi
  echo "Python 3.10–3.12 not found. Install Python 3.11 (recommended)." >&2
  echo "  macOS:  brew install python@3.11" >&2
  echo "  Linux:  sudo apt install python3.11 python3.11-venv" >&2
  exit 1
}

PYTHON_CMD="$(resolve_python)"

venv_python_ok() {
  local py="$1"
  [[ -x "$py" ]] || return 1
  local ok
  ok="$("$py" -c 'import sys; print("ok" if (3,10) <= sys.version_info[:2] <= (3,12) else "no")' 2>/dev/null || true)"
  [[ "$ok" == "ok" ]]
}

file_sha256() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  else
    echo "Neither shasum nor sha256sum found." >&2
    exit 1
  fi
}

ensure_venv() {
  local venv_dir="$1"
  local requirements="$2"
  local py="$venv_dir/bin/python"
  local hash_file="$venv_dir/.requirements.hash"

  if [[ -d "$venv_dir" && ! -f "$venv_dir/pyvenv.cfg" ]]; then
    echo "   Broken venv at $venv_dir — recreating once" >&2
    rm -rf "$venv_dir"
  elif [[ -x "$py" ]] && ! venv_python_ok "$py"; then
    echo "   Venv Python outside 3.10–3.12 at $venv_dir — recreating once" >&2
    rm -rf "$venv_dir"
  fi

  if [[ ! -x "$py" ]]; then
    echo ">> Creating $venv_dir (first run only)" >&2
    "$PYTHON_CMD" -m venv "$venv_dir"
  else
    echo "   Reusing $venv_dir" >&2
  fi

  local req_hash
  req_hash="$(file_sha256 "$requirements")"
  local prev=""
  [[ -f "$hash_file" ]] && prev="$(tr -d '[:space:]' < "$hash_file")"
  if [[ "$prev" != "$req_hash" ]]; then
    echo ">> Installing deps for $venv_dir" >&2
    "$py" -m pip install -q --upgrade pip
    "$py" -m pip install -q -r "$requirements"
    printf '%s' "$req_hash" > "$hash_file"
  fi

  printf '%s' "$py"
}

BACKEND_DIR="$ROOT/dashboard/gPBL/backend"
BACKEND_PY="$(ensure_venv "$BACKEND_DIR/.venv" "$BACKEND_DIR/requirements.txt")"
TRACK_PY="$(ensure_venv "$ROOT/tracking_AI/.venv" "$ROOT/tracking_AI/requirements.txt")"

echo ">> Checking config files..."
if [[ ! -f "$BACKEND_DIR/.env.example" ]]; then
  echo "MISSING: $BACKEND_DIR/.env.example" >&2
  exit 1
fi
[[ -f "$BACKEND_DIR/.env" ]] || cp "$BACKEND_DIR/.env.example" "$BACKEND_DIR/.env"

if [[ ! -f "$BACKEND_DIR/data/rules.json.example" ]]; then
  echo "MISSING: $BACKEND_DIR/data/rules.json.example" >&2
  exit 1
fi
mkdir -p "$BACKEND_DIR/data"
[[ -f "$BACKEND_DIR/data/rules.json" ]] || cp "$BACKEND_DIR/data/rules.json.example" "$BACKEND_DIR/data/rules.json"

if [[ ! -f "$BACKEND_DIR/serviceAccountKey.json" ]]; then
  echo ""
  echo "MISSING: backend/serviceAccountKey.json"
  echo "  Firebase Console -> Service accounts -> Generate key"
  echo "  Or: cp serviceAccountKey.json.example serviceAccountKey.json"
  exit 1
fi

if grep -q "OPENROUTER_API_KEY=sk-or-v1-your-key" "$BACKEND_DIR/.env" 2>/dev/null; then
  echo "   Note: OPENROUTER_API_KEY default — LLM uses mock until .env is edited"
fi

# Kill leftover uvicorn on 8080 only. Never steal the port from another app.
free_port_8080() {
  local pids pid cmd is_ours
  local ours="" foreign="" foreign_first=""
  if ! command -v lsof >/dev/null 2>&1; then
    return 0
  fi
  pids="$(lsof -ti:8080 2>/dev/null || true)"
  [[ -z "$pids" ]] && return 0

  for pid in $pids; do
    [[ "$pid" == "0" || "$pid" == "4" || "$pid" == "$$" ]] && continue
    cmd="$(ps -p "$pid" -o args= 2>/dev/null || true)"
    is_ours=0
    if [[ "$cmd" == *uvicorn* && "$cmd" == *main:app* ]]; then
      is_ours=1
    fi
    if [[ "$cmd" == *"$BACKEND_DIR/.venv"* ]]; then
      is_ours=1
    fi
    if [[ $is_ours -eq 1 ]]; then
      ours="$ours $pid"
    else
      foreign="$foreign $pid"
      [[ -z "$foreign_first" ]] && foreign_first="$pid"
    fi
  done

  if [[ -n "$foreign" ]]; then
    echo "Port 8080 is in use by another process (PID:$foreign)." >&2
    echo "Command: $(ps -p "$foreign_first" -o args= 2>/dev/null || echo unknown)" >&2
    echo "Stop that process yourself. Refusing to steal 8080." >&2
    exit 1
  fi

  for pid in $ours; do
    echo "   Stopping leftover uvicorn on 8080 (PID $pid)" >&2
    kill -TERM "$pid" 2>/dev/null || true
  done
  sleep 0.5
  for pid in $ours; do
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
  done
}

wait_for_8080() {
  local i=0
  while [[ $i -lt 20 ]]; do
    i=$((i + 1))
    if "$PYTHON_CMD" -c 'import socket,sys; s=socket.socket(); s.settimeout(0.4); r=s.connect_ex(("127.0.0.1",8080)); s.close(); sys.exit(0 if r==0 else 1)' 2>/dev/null; then
      return 0
    fi
    sleep 0.5
  done
  return 1
}

SERVER_PID=""
cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill -TERM "$SERVER_PID" 2>/dev/null || true
    sleep 0.3
    kill -KILL "$SERVER_PID" 2>/dev/null || true
    SERVER_PID=""
  fi
}
trap cleanup EXIT INT TERM

echo "======================================================================"
echo "PostureCare — dashboard + AI tracking"
echo "======================================================================"
echo "Web Python: $BACKEND_PY"
echo "CV  Python: $TRACK_PY"

echo "🔹 Freeing port 8080 (our uvicorn only)..."
free_port_8080

UVICORN_LOG="$BACKEND_DIR/uvicorn.log"
: > "$UVICORN_LOG"

echo "Starting FastAPI on http://localhost:8080/dashboard ..."
cd "$BACKEND_DIR"
"$BACKEND_PY" -m uvicorn main:app --host 0.0.0.0 --port 8080 >>"$UVICORN_LOG" 2>&1 &
SERVER_PID=$!
cd "$ROOT"

if ! wait_for_8080; then
  echo "FastAPI did not bind to :8080 in time. Last log lines:" >&2
  tail -n 40 "$UVICORN_LOG" >&2 || true
  exit 1
fi
echo "Dashboard: http://localhost:8080/dashboard"

CAM_ARG="${1:-0}"
echo "Launching AI tracking (source: $CAM_ARG)..."
echo "----------------------------------------------------------------------"
"$TRACK_PY" "$ROOT/tracking_AI/blink_counter_and_EAR_plot.py" "$CAM_ARG"
