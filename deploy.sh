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
  activity_log.py
  costs.py
  parked.py
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
  pid="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
  if [ -n "$pid" ]; then
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1 || break
      sleep 0.3
    done
    echo "  killed old server (pid $pid)"
  else
    echo "  no server was running on $PORT"
  fi
  nohup python3 "$DEST_DIR/quadmux-server.py" --port "$PORT" >"$DEST_DIR/server.log" 2>&1 &
  sleep 1
  newpid="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
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
