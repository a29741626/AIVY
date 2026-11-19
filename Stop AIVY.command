#!/bin/zsh

set -u

RUNTIME_DIR="/tmp/aivy-${UID}"

stop_from_pid_file() {
  local label="$1"
  local pid_file="$2"
  local pid

  if [[ ! -f "$pid_file" ]]; then
    print "$label was not started by the AIVY launcher."
    return
  fi

  pid="$(<"$pid_file")"
  if [[ "$pid" == <-> ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    print "$label stopped."
  else
    print "$label is already stopped."
  fi
  rm -f "$pid_file"
}

stop_from_pid_file "Frontend" "$RUNTIME_DIR/frontend.pid"
stop_from_pid_file "Backend" "$RUNTIME_DIR/backend.pid"
print "AIVY has been stopped."
sleep 2
