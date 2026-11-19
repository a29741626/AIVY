#!/bin/zsh

set -u

PROJECT_DIR="${0:A:h}"
RUNTIME_DIR="/tmp/aivy-${UID}"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
FRONTEND_PID_FILE="$RUNTIME_DIR/frontend.pid"
BACKEND_LOG="$RUNTIME_DIR/backend.log"
FRONTEND_LOG="$RUNTIME_DIR/frontend.log"
SITE_URL="http://127.0.0.1:5173/"

mkdir -p "$RUNTIME_DIR"

BACKEND_STARTED_PID=""
FRONTEND_STARTED_PID=""

cleanup() {
  local pid
  for pid in "$FRONTEND_STARTED_PID" "$BACKEND_STARTED_PID"; do
    if [[ "$pid" == <-> ]] && kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  [[ -n "$FRONTEND_STARTED_PID" ]] && rm -f "$FRONTEND_PID_FILE"
  [[ -n "$BACKEND_STARTED_PID" ]] && rm -f "$BACKEND_PID_FILE"
}

trap cleanup INT TERM EXIT

pause_on_error() {
  print ""
  print "AIVY could not start. Press Enter to close this window."
  read -r
  exit 1
}

port_is_ready() {
  curl --silent --fail --max-time 2 "$1" >/dev/null 2>&1
}

find_python() {
  local candidate
  for candidate in /opt/anaconda3/bin/python3 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
    if [[ -x "$candidate" ]] && "$candidate" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
      print "$candidate"
      return 0
    fi
  done
  candidate="$(command -v python3 2>/dev/null || true)"
  if [[ -n "$candidate" ]] && "$candidate" -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    print "$candidate"
    return 0
  fi
  return 1
}

find_node() {
  local candidate version major minor
  local -a candidates
  candidates=(
    "$HOME"/.nvm/versions/node/v*/bin/node(NOn)
    /opt/homebrew/bin/node
    /usr/local/bin/node
  )
  for candidate in "${candidates[@]}"; do
    [[ -x "$candidate" ]] || continue
    version="$($candidate -p 'process.versions.node' 2>/dev/null || true)"
    major="${version%%.*}"
    minor="${${version#*.}%%.*}"
    if [[ "$major" == <-> ]] && (( major > 22 || (major == 22 && minor >= 12) )); then
      print "$candidate"
      return 0
    fi
  done
  return 1
}

print "Starting AIVY..."

if ! port_is_ready "http://127.0.0.1:8000/docs"; then
  PYTHON_BIN="$(find_python)" || {
    print "Python with FastAPI and Uvicorn was not found."
    pause_on_error
  }
  (
    cd "$PROJECT_DIR/backend" || exit 1
    exec "$PYTHON_BIN" -m uvicorn main:app \
      --host 127.0.0.1 --port 8000 --env-file .env
  ) >"$BACKEND_LOG" 2>&1 &
  BACKEND_STARTED_PID=$!
  print "$BACKEND_STARTED_PID" >"$BACKEND_PID_FILE"
fi

if ! port_is_ready "$SITE_URL"; then
  NODE_BIN="$(find_node)" || {
    print "Node.js 22.12 or newer was not found."
    pause_on_error
  }
  if [[ ! -f "$PROJECT_DIR/frontend/node_modules/vite/bin/vite.js" ]]; then
    print "Frontend dependencies are missing. Run npm install in the frontend folder first."
    pause_on_error
  fi
  (
    cd "$PROJECT_DIR/frontend" || exit 1
    exec "$NODE_BIN" node_modules/vite/bin/vite.js --host 127.0.0.1
  ) >"$FRONTEND_LOG" 2>&1 &
  FRONTEND_STARTED_PID=$!
  print "$FRONTEND_STARTED_PID" >"$FRONTEND_PID_FILE"
fi

for _ in {1..60}; do
  if port_is_ready "http://127.0.0.1:8000/docs" && port_is_ready "$SITE_URL"; then
    print "AIVY is ready: $SITE_URL"
    open "$SITE_URL"
    if [[ -n "$BACKEND_STARTED_PID" || -n "$FRONTEND_STARTED_PID" ]]; then
      print "Keep this window open while using AIVY."
      print "Double-click Stop AIVY.command or press Control-C to stop."
      wait
    fi
    exit 0
  fi
  sleep 1
done

print "AIVY did not become ready in time."
print "Backend log: $BACKEND_LOG"
print "Frontend log: $FRONTEND_LOG"
pause_on_error
