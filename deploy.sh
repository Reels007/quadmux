#!/usr/bin/env bash
#
# deploy.sh - sync QuadMux runtime files from this source tree to the live
# install dir that the running server actually serves from.
#
# The server runs from "~/Library/Application Support/ClaudeX4/" (not this repo),
# so edits here have no effect until they are copied across. Run this after any
# change to quadmux.html or the *.py modules.
#
# Usage:
#   ./deploy.sh            copy files, then verify source and dest match
#   ./deploy.sh --restart  also restart the running server (needed for *.py changes;
#                           quadmux.html is re-read from disk on every request)
#
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST_DIR="$HOME/Library/Application Support/ClaudeX4"
PORT="${QUADMUX_PORT:-8766}"

# Runtime files the install actually uses. Keep this list in sync with what the
# server imports plus the HTML frontend.
FILES=(
  quadmux-server.py
  quadmux.html
  account_check.py
  activity_log.py
  costs.py
  parked.py
  policy.py
  presets.py
  sessions.py
  status_bus.py
  voice_routes.py
  worktree.py
)

if [ ! -d "$DEST_DIR" ]; then
  echo "ERROR: install dir not found: $DEST_DIR" >&2
  exit 1
fi

echo "Deploying from: $SRC_DIR"
echo "            to: $DEST_DIR"
echo

changed=0
for f in "${FILES[@]}"; do
  if [ ! -f "$SRC_DIR/$f" ]; then
    echo "  SKIP (missing in source): $f"
    continue
  fi
  if [ -f "$DEST_DIR/$f" ] && diff -q "$SRC_DIR/$f" "$DEST_DIR/$f" >/dev/null 2>&1; then
    echo "  ok   $f"
  else
    cp "$SRC_DIR/$f" "$DEST_DIR/$f"
    echo "  COPY $f"
    changed=$((changed + 1))
  fi
done

echo
echo "Verifying (source vs deployed)..."
drift=0
for f in "${FILES[@]}"; do
  [ -f "$SRC_DIR/$f" ] || continue
  if ! diff -q "$SRC_DIR/$f" "$DEST_DIR/$f" >/dev/null 2>&1; then
    echo "  DRIFT: $f"
    drift=$((drift + 1))
  fi
done
if [ "$drift" -eq 0 ]; then
  echo "  in sync ($changed file(s) updated)"
else
  echo "  FAILED: $drift file(s) still differ" >&2
  exit 1
fi

if [ "${1:-}" = "--restart" ]; then
  echo
  echo "Restarting server on port $PORT..."
  echo "  NOTE: this stops all 4 Claude panes. The server writes"
  echo "        ~/.quadmux/resume_next.json on shutdown, so the new one resumes"
  echo "        each pane's conversation instead of starting them empty."
  pid="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    # SIGTERM now runs graceful_shutdown: write the resume map, save the session
    # buffers, then SIGTERM/SIGKILL the four children. That takes a few seconds,
    # so wait on the process itself rather than racing it for the port.
    gone=0
    for _ in $(seq 1 40); do
      if ! kill -0 "$pid" 2>/dev/null; then gone=1; break; fi
      sleep 0.5
    done
    if [ "$gone" -eq 1 ]; then
      echo "  stopped old server cleanly (pid $pid)"
    else
      echo "  old server (pid $pid) ignored SIGTERM after 20s, forcing" >&2
      kill -9 "$pid" 2>/dev/null || true
      sleep 1
    fi
    # The listener can linger a moment after the process goes.
    for _ in $(seq 1 20); do
      lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1 || break
      sleep 0.5
    done
    if [ -f "$HOME/.quadmux/resume_next.json" ]; then
      echo "  resume map present: panes will reattach to their sessions"
    else
      echo "  WARNING: no resume map written, panes will start fresh" >&2
    fi
  else
    echo "  no server was running on $PORT"
  fi
  nohup python3 "$DEST_DIR/quadmux-server.py" --port "$PORT" >"$DEST_DIR/server.log" 2>&1 &
  # Startup staggers pane spawns 1.5s apart, so the port can take several
  # seconds to open; poll up to 15s instead of checking once.
  newpid=""
  for _ in $(seq 1 30); do
    newpid="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
    [ -n "$newpid" ] && break
    sleep 0.5
  done
  if [ -n "$newpid" ]; then
    echo "  started server (pid $newpid), log: $DEST_DIR/server.log"
  else
    echo "  WARNING: server did not come up, check $DEST_DIR/server.log" >&2
    exit 1
  fi
else
  echo
  echo "Done. quadmux.html is served fresh per request (just hard-refresh the tab)."
  echo "For *.py changes, re-run with --restart to reload the server."
fi
