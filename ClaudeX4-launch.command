#!/bin/bash
# ClaudeX4 - Launch 4 Claude instances in QuadMux with voice preflight
set -u

LOG=/tmp/quadmux.log
: > "$LOG"
exec >>"$LOG" 2>&1

echo "=== ClaudeX4 launch $(date) ==="
# This script is bash (universal), so its own arch is what LaunchServices chose
# for the whole process tree. x86_64 here means the tree runs under Rosetta.
echo "launch-arch=$(uname -m) translated=$(sysctl -n sysctl.proc_translated 2>/dev/null || echo 0)"

# 0. Where QuadMux runs. The Mac mini is the dev machine (6 Nov 2026).
# On the mini: the server binds localhost and `tailscale serve` publishes it on
# HTTPS (needed for the browser mic), with /whisper and /kokoro mounted on the
# same origin. On any other machine: just open the mini's HTTPS URL.
# QUADMUX_HOST=localhost forces the old local-only mode on any machine.
MINI_IP=100.88.87.92
MINI_URL="https://mac-mini.tailb087a9.ts.net:8443"
HOST="${QUADMUX_HOST:-$MINI_IP}"
PORT=8766
TS=$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)
if [ "$HOST" != "localhost" ] && ! ifconfig | grep -q "inet $HOST "; then
    echo "Remote mode: opening QuadMux at $MINI_URL"
    code=000
    for i in $(seq 1 10); do
        code=$(curl -s -m 3 -o /dev/null -w '%{http_code}' "$MINI_URL" 2>/dev/null || echo 000)
        [ "$code" = "200" ] && break
        sleep 1
    done
    if [ "$code" != "200" ]; then
        osascript -e "display alert \"QuadMux\" message \"Cannot reach QuadMux at $MINI_URL. Check the mini is on, Tailscale is connected, and ClaudeX4 is running on the mini.\""
        exit 1
    fi
    open "$MINI_URL"
    exit 0
fi
ON_MINI=0
[ "$HOST" = "$MINI_IP" ] && ON_MINI=1
HOST=localhost

# 0b. Pane guard. Every QuadMux pane shell descends from quadmux-server.py, so the
# unconditional pkill below would kill the server hosting the very session that ran
# this script. If the server is already serving and we are one of its descendants,
# open the UI and stop. Added 7 Nov 2026 after two panes killed themselves this way.
QX_PID=$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)
if [ -n "${QX_PID:-}" ]; then
    anc=$$
    while [ -n "$anc" ] && [ "$anc" -gt 1 ] 2>/dev/null; do
        if [ "$anc" = "$QX_PID" ]; then
            echo "Pane guard: QuadMux already serving as PID $QX_PID and this shell descends from it."
            echo "Opening UI only, not restarting. Unset the guard by running from outside a pane."
            open "http://$HOST:$PORT"
            exit 0
        fi
        anc=$(ps -p "$anc" -o ppid= 2>/dev/null | tr -d ' ')
    done
fi

# 1. Stop any previous QuadMux
pkill -f "quadmux-server.py" 2>/dev/null || true
sleep 1

# 2. PATH + pull ElevenLabs key from shell config (key now lives in ~/.zshenv)
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ -z "${ELEVENLABS_API_KEY:-}" ]; then
    for cfg in "$HOME/.zshenv" "$HOME/.zshrc" "$HOME/.bash_profile"; do
        [ -f "$cfg" ] || continue
        KEY_LINE=$(grep -E '^export ELEVENLABS_API_KEY=' "$cfg" | tail -1 || true)
        if [ -n "$KEY_LINE" ]; then
            eval "$KEY_LINE"
            export ELEVENLABS_API_KEY
            break
        fi
    done
fi
_EL_KEY="${ELEVENLABS_API_KEY:-}"
echo "ELEVENLABS_API_KEY length: ${#_EL_KEY}"

# 3. Sync source to non-TCC location
SRC="$HOME/Projects/quadmux"
DEST="$HOME/Library/Application Support/ClaudeX4"
mkdir -p "$DEST"

sync_ok=1
# Sync all Python modules + the HTML UI. New modules (status_bus, presets,
# worktree, parked, costs, activity_log, sessions) must be copied too.
for f in $(cd "$SRC" 2>/dev/null && ls *.py *.html 2>/dev/null); do
    if ! cp "$SRC/$f" "$DEST/" 2>/dev/null; then
        [ -f "$DEST/$f" ] || sync_ok=0
    fi
done
if [ "$sync_ok" -ne 1 ]; then
    osascript -e 'display alert "QuadMux" message "Cannot access Desktop files. Grant Full Disk Access to ClaudeX4 in System Settings > Privacy & Security."'
    exit 1
fi

# 4. Voice preflight - JARVIS uses ElevenLabs directly, so only Whisper STT needed.
# Start Whisper async and wait briefly; never block the UI on it.
VOICEMODE="$HOME/.local/bin/voicemode"
port_open() { nc -z localhost "$1" 2>/dev/null; }

if ! port_open 2022; then
    echo "Starting Whisper STT..."
    "$VOICEMODE" service start whisper >/dev/null 2>&1 &
fi

for i in $(seq 1 10); do
    if port_open 2022; then
        echo "Whisper ready after ${i}s"
        break
    fi
    sleep 1
done
port_open 2022 || echo "WARN: Whisper not up on :2022 yet (continuing, STT will catch up async)"

# 5. Start QuadMux (port 8766, avoids voicemode MCP on 8765)
# On the mini a KeepAlive LaunchAgent owns the server; restart it rather than
# starting a second copy that would fight launchd for the port.
QX_AGENT="$HOME/Library/LaunchAgents/com.seanreel.quadmux.plist"
if [ "$ON_MINI" = 1 ] && [ -f "$QX_AGENT" ]; then
    launchctl print "gui/$(id -u)/com.seanreel.quadmux" >/dev/null 2>&1 || launchctl bootstrap "gui/$(id -u)" "$QX_AGENT"
    launchctl kickstart -k "gui/$(id -u)/com.seanreel.quadmux"
    SERVER_PID=launchd
else
    /opt/homebrew/bin/python3 "$DEST/quadmux-server.py" --host "$HOST" --port "$PORT" &
    SERVER_PID=$!
fi
echo "quadmux-server.py PID $SERVER_PID on port $PORT"

# 6. Wait for QuadMux UI to actually serve
for i in $(seq 1 30); do
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://$HOST:$PORT" 2>/dev/null || echo 000)
    if [ "$code" = "200" ]; then
        echo "QuadMux serving after ${i}s"
        break
    fi
    sleep 1
done

# 7. Open the UI
if [ "$ON_MINI" = "1" ]; then
    # Idempotent; --bg config persists across reboots.
    "$TS" serve --bg http://localhost:$PORT >/dev/null 2>&1 \
      && "$TS" serve --bg --set-path /whisper http://localhost:2022 >/dev/null 2>&1 \
      && "$TS" serve --bg --set-path /kokoro http://localhost:8880 >/dev/null 2>&1 \
      && echo "tailscale serve: $MINI_URL -> localhost:$PORT" \
      || echo "WARN: tailscale serve failed (is HTTPS enabled for the tailnet?)"
fi
open "http://$HOST:$PORT"
