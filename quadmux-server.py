#!/usr/bin/env python3
"""QuadMux Server - Run multiple Claude Code instances via pty.fork() + xterm.js WebSocket.

Usage:
    python3 quadmux-server.py [--shells N] [--port PORT]

Requires: pip install websockets
"""

import argparse
import asyncio
import base64
import json
import os
import pty
import re
import signal
import shutil
import socket
import struct
import fcntl
import termios
import select
import sys
import threading
import subprocess
import time
import uuid

UPLOAD_DIR = os.path.expanduser("~/.quadmux/uploads")
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB cap per file
_SAFE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_upload_path(filename: str) -> str:
    base = os.path.basename(filename or "file")
    base = _SAFE_NAME_RE.sub("_", base).strip("._") or "file"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    return os.path.join(UPLOAD_DIR, f"{int(time.time())}_{uuid.uuid4().hex[:6]}_{base}")

# Unique per-server-process token. Embedded in HTML and sent in WebSocket hello
# so any browser tab from a previous server process force-reloads instead of
# silently running stale JavaScript.
SERVER_VERSION = uuid.uuid4().hex[:12]

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
    import websockets

import re

from status_bus import StatusBus
import presets as presets_mod
import worktree as worktree_mod
import parked as parked_mod
import costs as costs_mod
import activity_log as activity_mod
import sessions as sessions_mod

masters = []
child_pids = []
clients = set()
loop = None
shell_buffers = []
_save_lock = threading.Lock()
MAX_BUFFER = 200
NUM_SHELLS = 4
bus = None  # initialised in main() once NUM_SHELLS is final
BUS_TICK_INTERVAL = 1.0

# Phase 3: per-pane metadata (role, cwd, branch). Populated when --preset is used.
pane_meta = []  # list of dicts indexed by shell idx
worktree_repo = None  # path to the source repo if worktrees were created
worktree_session_id = None  # for prune-on-shutdown

# Phase 5: per-pane cost trackers
cost_trackers = []  # list of costs_mod.CostTracker
COST_POLL_INTERVAL = 5.0  # seconds
SESSION_LOOKUP_RETRY_INTERVAL = 8.0  # seconds before re-scanning for missing session files
SESSION_DIR = os.path.join(os.path.expanduser("~"), ".quadmux", "sessions")
AUTOSAVE_INTERVAL = 30  # seconds

# Phase 6: current session id and the per-run subdirectory we write to.
session_id: str = ""
session_dir: str = ""
session_started_at: float = 0.0

# --- Voice response extraction (server-side) ---
ANSI_RE = re.compile(r'\x1b(?:\[[0-9;]*[a-zA-Z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-2AB]|[>=<78DEHM])')

voice_shell = -1           # which shell has voice active (-1 = none)
voice_capturing = {}       # shell -> bool, are we capturing output after voice input?
voice_buffers = {}         # shell -> str, accumulated raw output since voice input
voice_timers = {}          # shell -> asyncio.TimerHandle for settling timeout

def _prose_lines(raw_text):
    """Internal: yield filtered prose lines from raw PTY output."""
    clean = ANSI_RE.sub('', raw_text)
    clean = re.sub(r'[─│┌┐└┘├┤┬┴┼╭╮╯╰▓░▒█]', '', clean)
    clean = re.sub(r'[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏●◯✓✗✔✘⚡❯▶◀▲▼]', '', clean)
    out = []
    for line in clean.split('\n'):
        line = line.strip()
        if len(line) < 10:
            continue
        if not line[0].isalpha():
            continue
        good = sum(1 for c in line if c.isalpha() or c in ' ,.\'"!?;:-')
        if good / len(line) < 0.75:
            continue
        lower = line.lower()
        if any(x in lower for x in ['calculat', 'token', 'cost:', 'allow', 'deny',
                                      'read(', 'write(', 'edit(', 'bash(', 'glob(', 'grep(',
                                      '.py:', '.js:', '.ts:', '.html:', 'localhost',
                                      'http://', 'https://', '```']):
            continue
        out.append(line)
    return out


def extract_recent_prose(shell_idx: int, max_lines: int = 40) -> str:
    """Pull up to `max_lines` recent prose lines from a shell's output buffer."""
    if not (0 <= shell_idx < len(shell_buffers)):
        return ""
    raw = "".join(shell_buffers[shell_idx][-30:])
    lines = _prose_lines(raw)
    if not lines:
        return ""
    return "\n".join(lines[-max_lines:])


def extract_prose(raw_text):
    """Extract clean prose from raw terminal output. Returns text suitable for TTS."""
    clean = ANSI_RE.sub('', raw_text)
    # Remove common terminal artifacts
    clean = re.sub(r'[─│┌┐└┘├┤┬┴┼╭╮╯╰▓░▒█]', '', clean)
    clean = re.sub(r'[⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏●◯✓✗✔✘⚡❯▶◀▲▼]', '', clean)
    lines = clean.split('\n')
    prose = []
    for line in lines:
        line = line.strip()
        if len(line) < 10:
            continue
        # Must start with a letter
        if not line[0].isalpha():
            continue
        # Count alphabetic + basic punctuation
        good = sum(1 for c in line if c.isalpha() or c in ' ,.\'"!?;:-')
        if good / len(line) < 0.75:
            continue
        # Skip common UI/tool lines
        lower = line.lower()
        if any(x in lower for x in ['calculat', 'token', 'cost:', 'allow', 'deny',
                                      'read(', 'write(', 'edit(', 'bash(', 'glob(', 'grep(',
                                      '.py:', '.js:', '.ts:', '.html:', 'localhost',
                                      'http://', 'https://', '```']):
            continue
        prose.append(line)
    # Return last 4 prose lines joined
    return '. '.join(prose[-4:]) if prose else ''


async def voice_output_settled(shell_idx):
    """Called when output stops flowing for a shell with voice active."""
    if shell_idx not in voice_buffers or not voice_buffers[shell_idx]:
        print(f"  Voice: no buffer for shell {shell_idx}", flush=True)
        return
    raw = voice_buffers[shell_idx]
    print(f"  Voice: output settled for shell {shell_idx}, buffer={len(raw)} chars", flush=True)

    response = extract_prose(raw)
    print(f"  Voice: extracted prose: {repr(response[:100]) if response else '(empty)'}", flush=True)

    if response and len(response) > 10:
        # Clear buffer and stop capturing - we got a response
        voice_buffers[shell_idx] = ''
        voice_capturing[shell_idx] = False
        msg = json.dumps({"type": "voice_response", "shell": shell_idx, "text": response})
        print(f"  Voice: sending TTS ({len(response)} chars)", flush=True)
        for ws in clients.copy():
            try:
                await ws.send(msg)
            except Exception:
                pass
    else:
        # No prose yet - keep capturing, Claude might still be working
        print(f"  Voice: no prose found, keeping capture active", flush=True)


def save_session():
    """Save shell buffers to the current session's archive dir."""
    target = session_dir or SESSION_DIR
    with _save_lock:
        os.makedirs(target, exist_ok=True)
        for idx in range(NUM_SHELLS):
            path = os.path.join(target, f"shell_{idx}.json")
            tmp_path = path + ".tmp"
            try:
                with open(tmp_path, "w") as f:
                    # Snapshot the buffer to avoid mutation during serialization
                    buf_copy = list(shell_buffers[idx])
                    json.dump({"buffer": buf_copy, "saved_at": time.time()}, f)
                os.replace(tmp_path, path)
            except (OSError, TypeError) as e:
                print(f"  Save error shell {idx}: {e}", flush=True)
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        print(f"  Session saved -> {os.path.basename(target)} ({NUM_SHELLS} shells)", flush=True)


def load_session():
    """Load buffers from the most recent previous session for continuity."""
    loaded = [[] for _ in range(NUM_SHELLS)]
    prev = sessions_mod.previous_session_dir(skip_id=session_id or None)
    if not prev:
        # Fallback for legacy flat layout (pre-phase-6)
        legacy_files = [os.path.join(SESSION_DIR, f"shell_{i}.json")
                        for i in range(NUM_SHELLS)]
        if any(os.path.exists(p) for p in legacy_files):
            prev = SESSION_DIR
    if not prev:
        return loaded
    for idx in range(NUM_SHELLS):
        path = os.path.join(prev, f"shell_{idx}.json")
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                raw = f.read()
            if not raw.strip():
                continue
            data = json.loads(raw)
            loaded[idx] = data.get("buffer", [])
            saved = data.get("saved_at", 0)
            age = time.time() - saved
            print(f"  Loaded shell {idx} from {os.path.basename(prev)} "
                  f"({len(loaded[idx])} chunks, {age:.0f}s old)", flush=True)
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as e:
            print(f"  Load error shell {idx}: {e}", flush=True)
    return loaded


async def autosave_loop():
    """Periodically save session state to disk."""
    while True:
        await asyncio.sleep(AUTOSAVE_INTERVAL)
        save_session()


def kill_stale_server(port):
    """Kill any existing QuadMux process on the target port."""
    try:
        result = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            pids = set(result.stdout.strip().split("\n"))
            my_pid = str(os.getpid())
            for pid in pids:
                if pid and pid != my_pid:
                    try:
                        os.kill(int(pid), signal.SIGTERM)
                        print(f"  Killed stale process {pid} on port {port}", flush=True)
                    except (OSError, ValueError):
                        pass
            # Wait for processes to die, with verification
            for _ in range(10):
                time.sleep(0.2)
                if port_is_free(port):
                    return
            # Force kill if SIGTERM didn't work
            for pid in pids:
                if pid and pid != my_pid:
                    try:
                        os.kill(int(pid), signal.SIGKILL)
                    except (OSError, ValueError):
                        pass
            time.sleep(0.3)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def port_is_free(port):
    """Check if a port is available."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("localhost", port))
            return True
        except OSError:
            return False


def find_claude():
    """Auto-detect the claude CLI path."""
    # Check common locations
    path = shutil.which("claude")
    if path:
        # Use the symlink path directly - resolving breaks shebang-based scripts
        # (e.g. cli.js needs #!/usr/bin/env node via the symlink, not direct execv)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    home = os.path.expanduser("~")
    for candidate in [
        os.path.join(home, ".local", "bin", "claude"),
        os.path.join(home, ".claude", "bin", "claude"),
        "/usr/local/bin/claude",
        "/opt/homebrew/bin/claude",
    ]:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    print("Error: 'claude' not found. Install Claude Code first: https://docs.anthropic.com/en/docs/claude-code")
    sys.exit(1)


def spawn_claude(claude_path, idx, rows=24, cols=80, cwd=None, extra_args=None):
    """Spawn a Claude Code instance via pty.fork(). Verifies child is alive.

    ``cwd``: optional working directory the child chdirs into before exec.
    ``extra_args``: optional list of extra CLI args appended after argv[0].
    """
    pid, master_fd = pty.fork()
    if pid == 0:
        # Child process - ensure full PATH for shebang scripts (macOS .app has minimal PATH)
        path = os.environ.get("PATH", "")
        for extra in ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")]:
            if extra not in path:
                path = extra + ":" + path
        os.environ["PATH"] = path
        os.environ.pop("ANTHROPIC_API_KEY", None)
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLORTERM"] = "truecolor"
        if cwd:
            try:
                os.chdir(cwd)
            except OSError as e:
                sys.stderr.write(f"chdir({cwd}) failed: {e}\n")
                os._exit(126)
        argv = [claude_path] + list(extra_args or [])
        try:
            os.execv(claude_path, argv)
        except OSError as e:
            # If execv fails, write error and exit child cleanly
            sys.stderr.write(f"execv failed: {e}\n")
            os._exit(127)
    else:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass
        # Verify child didn't die immediately (bad path, missing deps, etc.)
        # Poll up to 1.5s with short intervals instead of fixed sleep
        for _ in range(15):
            time.sleep(0.1)
            try:
                result = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                os.close(master_fd)
                raise RuntimeError(f"Claude {idx+1} child process vanished")
            if result[0] != 0:
                os.close(master_fd)
                exit_code = os.WEXITSTATUS(result[1]) if os.WIFEXITED(result[1]) else -1
                raise RuntimeError(f"Claude {idx+1} died immediately (exit {exit_code})")
        return pid, master_fd


def pty_reader_thread(master_fd, idx):
    """Thread that reads from PTY master and schedules broadcast."""
    print(f"  Reader {idx} started (fd={master_fd})", flush=True)
    while True:
        try:
            r, _, _ = select.select([master_fd], [], [], 0.1)
            if r:
                try:
                    data = os.read(master_fd, 65536)
                except OSError:
                    break
                if not data:
                    break
                text = data.decode("utf-8", errors="replace")
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(broadcast(idx, text), loop)
        except (OSError, ValueError):
            break
    print(f"  Reader {idx} stopped", flush=True)


async def broadcast(idx, text):
    shell_buffers[idx].append(text)
    if len(shell_buffers[idx]) > MAX_BUFFER:
        shell_buffers[idx] = shell_buffers[idx][-MAX_BUFFER:]
    msg = json.dumps({"type": "output", "shell": idx, "text": text})
    dead_clients = []
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except (websockets.ConnectionClosed, ConnectionError):
            dead_clients.append(ws)
    for ws in dead_clients:
        clients.discard(ws)

    # Status bus: feed output to the detector and fan out state changes
    if bus is not None:
        prior_state = bus.states[idx]
        new_state = bus.update(idx, text)
        if new_state:
            await _broadcast_state(idx, new_state)
            # Open or close a permission request on the appropriate transition
            if new_state == "awaiting_permission" and prior_state != "awaiting_permission":
                from status_bus import extract_permission_question
                question = extract_permission_question(
                    "".join(shell_buffers[idx][-8:]) + text
                )
                req = bus.open_permission(idx, question)
                await _broadcast_permission_request(req)
            elif prior_state == "awaiting_permission" and new_state != "awaiting_permission":
                req = bus.close_permission(idx, reason="state_change")
                if req:
                    await _broadcast_permission_resolved(req["id"], idx, "state_change")

    # Voice: accumulate output if capturing
    if voice_capturing.get(idx):
        voice_buffers[idx] = voice_buffers.get(idx, '') + text
        # Cap buffer
        if len(voice_buffers[idx]) > 10000:
            voice_buffers[idx] = voice_buffers[idx][-10000:]
        # Check for Claude prompt (❯) in the raw stream - means response is complete
        clean_tail = ANSI_RE.sub('', voice_buffers[idx][-200:]) if len(voice_buffers[idx]) > 50 else ''
        # Only match the actual Claude Code prompt: ❯ (U+276F) near end of line
        has_prompt = '\u276f' in clean_tail
        if has_prompt:
            # Reset settle timer - short delay after prompt detected
            if idx in voice_timers and voice_timers[idx]:
                voice_timers[idx].cancel()
            voice_timers[idx] = loop.call_later(1.0, lambda i=idx: asyncio.ensure_future(voice_output_settled(i)))


async def _broadcast_state(shell_idx, state, ts=None):
    """Send a state change event to every connected client."""
    msg = json.dumps({"type": "state", "shell": shell_idx,
                      "state": state, "ts": ts or time.time()})
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except (websockets.ConnectionClosed, ConnectionError):
            clients.discard(ws)


async def _broadcast_permission_request(req):
    msg = json.dumps({"type": "permission_request", **req})
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except (websockets.ConnectionClosed, ConnectionError):
            clients.discard(ws)


async def _broadcast_parked_update(task, action="update"):
    msg = json.dumps({"type": "parked_" + action, "task": task})
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except (websockets.ConnectionClosed, ConnectionError):
            clients.discard(ws)


async def _broadcast_parked_delete(task_id):
    msg = json.dumps({"type": "parked_delete", "id": task_id})
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except (websockets.ConnectionClosed, ConnectionError):
            clients.discard(ws)


async def _broadcast_handoff(source, target, instruction):
    msg = json.dumps({"type": "handoff", "source": source, "target": target,
                      "instruction": instruction, "ts": time.time()})
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except (websockets.ConnectionClosed, ConnectionError):
            clients.discard(ws)


async def _broadcast_permission_resolved(req_id, shell_idx, reason):
    msg = json.dumps({"type": "permission_resolved", "id": req_id,
                      "shell": shell_idx, "reason": reason, "ts": time.time()})
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except (websockets.ConnectionClosed, ConnectionError):
            clients.discard(ws)


async def bus_tick_loop():
    """Periodically decay stale 'thinking'/'tool_running' states to 'idle'."""
    while True:
        await asyncio.sleep(BUS_TICK_INTERVAL)
        if bus is None:
            continue
        for change in bus.tick():
            await _broadcast_state(change["shell"], change["to"], change["ts"])


def _cost_snapshot_payload():
    """Build the full cost-snapshot WS payload (per-pane + totals)."""
    panes = []
    total_tokens = 0
    total_cost = 0.0
    for i, t in enumerate(cost_trackers):
        snap = t.snapshot() if t else {"tokens": {"input": 0, "output": 0,
                                                   "cache_read": 0, "cache_write": 0},
                                       "total_tokens": 0, "cost": 0.0, "model": ""}
        panes.append({"shell": i, **snap,
                      "has_session": bool(t and t.path)})
        total_tokens += snap["total_tokens"]
        total_cost += snap["cost"]
    return {"type": "cost_snapshot", "panes": panes,
            "total_tokens": total_tokens, "total_cost": round(total_cost, 4)}


async def _broadcast_cost():
    payload = json.dumps(_cost_snapshot_payload())
    for ws in clients.copy():
        try:
            await ws.send(payload)
        except (websockets.ConnectionClosed, ConnectionError):
            clients.discard(ws)


async def cost_poll_loop():
    """Poll each pane's session JSONL for new usage events."""
    last_lookup = 0.0
    while True:
        await asyncio.sleep(COST_POLL_INTERVAL)
        if not cost_trackers:
            continue
        # If any pane lacks a session file yet, retry the lookup periodically.
        if any(t and not t.path for t in cost_trackers):
            now = time.time()
            if now - last_lookup >= SESSION_LOOKUP_RETRY_INTERVAL:
                last_lookup = now
                # Panes spawned without a preset inherit the server's cwd.
                default_cwd = os.getcwd()
                cwds = [(pane_meta[i].get("cwd") if i < len(pane_meta) else "") or default_cwd
                        for i in range(NUM_SHELLS)]
                sids = [(pane_meta[i].get("session_id") if i < len(pane_meta) else "") or ""
                        for i in range(NUM_SHELLS)]
                # Deterministic: each pane was spawned with --session-id, so its
                # JSONL filename is known exactly.
                for i, t in enumerate(cost_trackers):
                    if t and not t.path and sids[i]:
                        p = costs_mod.session_file_for_id(cwds[i], sids[i])
                        if p:
                            t.attach(p)
                            print(f"  Cost: pane {i+1} attached to {p}", flush=True)
                # Fallback for panes without a session id: newest-first by cwd.
                if any(t and not t.path and not sids[i]
                       for i, t in enumerate(cost_trackers)):
                    paths = costs_mod.assign_session_files(cwds)
                    for i, p in enumerate(paths):
                        if (cost_trackers[i] and not cost_trackers[i].path
                                and not sids[i] and p):
                            cost_trackers[i].attach(p)
                            print(f"  Cost: pane {i+1} attached to {p}", flush=True)
        changed = False
        for t in cost_trackers:
            if t and t.poll():
                changed = True
        if changed:
            await _broadcast_cost()


async def broadcast_error(idx, text):
    """Broadcast an error message for a specific shell."""
    msg = json.dumps({"type": "output", "shell": idx, "text": f"\r\n\x1b[31m[QuadMux] {text}\x1b[0m\r\n"})
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except (websockets.ConnectionClosed, ConnectionError):
            clients.discard(ws)


def get_html_content():
    """Load the HTML frontend with the server version stamp injected."""
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quadmux.html")
    if os.path.exists(html_path):
        with open(html_path) as f:
            return f.read().replace("__SERVER_VERSION__", SERVER_VERSION)
    return "<html><body><h1>quadmux.html not found</h1></body></html>"


async def http_handler(connection, request):
    """Serve API routes or the HTML UI on HTTP requests (non-WebSocket)."""
    if request.headers.get("Upgrade") is not None:
        return  # let websockets handle the upgrade

    from websockets.http11 import Response
    from websockets.datastructures import Headers

    try:
        from voice_routes import dispatch
        route_result = await dispatch(request)
    except Exception as e:
        print(f"  voice_routes.dispatch error: {e}", flush=True)
        route_result = None

    if route_result is not None:
        status, header_pairs, body = route_result
        return Response(status, "OK" if status == 200 else "ERR",
                        Headers(list(header_pairs)), body)

    return Response(200, "OK", Headers([
        ("Content-Type", "text/html"),
        ("Cache-Control", "no-cache, no-store, must-revalidate"),
        ("Pragma", "no-cache"),
        ("Expires", "0"),
    ]), get_html_content().encode())


async def handler(ws):
    global voice_shell
    clients.add(ws)
    print(f"Client connected ({len(clients)} total) version={SERVER_VERSION}", flush=True)
    try:
        await ws.send(json.dumps({"type": "hello", "version": SERVER_VERSION}))
    except websockets.ConnectionClosed:
        clients.discard(ws)
        return
    # Replay buffered output - snapshot each buffer to avoid mutation during replay
    for idx in range(NUM_SHELLS):
        buf_snapshot = list(shell_buffers[idx])
        for chunk in buf_snapshot:
            try:
                await ws.send(json.dumps({"type": "output", "shell": idx, "text": chunk}))
            except websockets.ConnectionClosed:
                clients.discard(ws)
                return
    # Send current per-pane state snapshot so the UI badges hydrate immediately
    if bus is not None:
        try:
            await ws.send(json.dumps({"type": "state_snapshot", "states": bus.snapshot()}))
            await ws.send(json.dumps({"type": "permission_snapshot",
                                      "requests": bus.open_permissions()}))
            # Pane metadata (role/cwd/branch) for preset-driven sessions
            meta_payload = [
                {
                    "shell": i,
                    "role": (pane_meta[i].get("role") if i < len(pane_meta) else "") or "",
                    "cwd": (pane_meta[i].get("cwd") if i < len(pane_meta) else "") or "",
                    "branch": (pane_meta[i].get("branch") if i < len(pane_meta) else "") or "",
                }
                for i in range(NUM_SHELLS)
            ]
            await ws.send(json.dumps({"type": "pane_meta", "panes": meta_payload}))
            # Parked tasks sidebar
            await ws.send(json.dumps({"type": "parked_list",
                                      "tasks": parked_mod.list_tasks()}))
            # Cost snapshot (per-pane tokens + total)
            await ws.send(json.dumps(_cost_snapshot_payload()))
        except websockets.ConnectionClosed:
            clients.discard(ws)
            return
    # Force redraw by toggling size
    for fd in masters:
        try:
            winsize = struct.pack("HHHH", 23, 79, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
            winsize = struct.pack("HHHH", 24, 80, 0, 0)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass
    try:
        async for message in ws:
            try:
                data = json.loads(message)
            except (json.JSONDecodeError, TypeError):
                continue
            msg_type = data.get("type")
            if msg_type == "input":
                idx = data.get("shell")
                text = data.get("text", "")
                raw = data.get("raw", False)
                if idx is not None and 0 <= idx < NUM_SHELLS:
                    # If this shell has voice active, start capturing response
                    if idx == voice_shell and not raw:
                        voice_capturing[idx] = True
                        voice_buffers[idx] = ''
                        print(f"  Voice: capturing started for shell {idx} (input: {text[:50]})", flush=True)
                    try:
                        payload = text if raw else text + "\r"
                        os.write(masters[idx], payload.encode())
                    except (OSError, IndexError) as e:
                        print(f"  Write error shell {idx}: {e}", flush=True)
            elif msg_type == "upload":
                idx = data.get("shell")
                filename = data.get("filename", "file")
                b64 = data.get("data", "")
                if idx is None or not (0 <= idx < NUM_SHELLS):
                    continue
                try:
                    raw = base64.b64decode(b64, validate=False)
                except Exception as e:
                    await ws.send(json.dumps({"type": "upload_error", "shell": idx,
                                              "filename": filename, "error": f"decode: {e}"}))
                    continue
                if len(raw) > MAX_UPLOAD_BYTES:
                    await ws.send(json.dumps({"type": "upload_error", "shell": idx,
                                              "filename": filename,
                                              "error": f"file too large ({len(raw)} bytes)"}))
                    continue
                try:
                    dest = _safe_upload_path(filename)
                    with open(dest, "wb") as fh:
                        fh.write(raw)
                    print(f"  Upload shell {idx}: {filename} -> {dest} ({len(raw)} bytes)", flush=True)
                    await ws.send(json.dumps({"type": "upload_done", "shell": idx,
                                              "filename": filename, "path": dest}))
                except OSError as e:
                    await ws.send(json.dumps({"type": "upload_error", "shell": idx,
                                              "filename": filename, "error": str(e)}))
            elif msg_type == "session_list":
                try:
                    items = sessions_mod.list_sessions()
                except OSError:
                    items = []
                await ws.send(json.dumps({"type": "session_list",
                                          "sessions": items,
                                          "current": session_id}))
            elif msg_type == "session_replay":
                sid = data.get("id") or ""
                if not isinstance(sid, str) or not sid:
                    continue
                # Cap buffer payload size so big archives don't blow the socket.
                replay = sessions_mod.load_replay(sid, NUM_SHELLS)
                if replay.get("buffers"):
                    capped = []
                    for buf in replay["buffers"]:
                        joined = "".join(buf)[-200_000:]  # last ~200KB per pane
                        capped.append(joined)
                    replay["buffers"] = capped
                await ws.send(json.dumps({"type": "session_replay", **replay}))
            elif msg_type == "activity_request":
                limit = data.get("limit") or 200
                shell_filter = data.get("shell")
                types_filter = data.get("types")
                try:
                    events = activity_mod.recent_events(
                        limit=int(limit),
                        shell=shell_filter if isinstance(shell_filter, int) else None,
                        event_types=list(types_filter) if isinstance(types_filter, list) else None,
                    )
                except (ValueError, TypeError):
                    events = []
                await ws.send(json.dumps({"type": "activity_response",
                                          "events": events}))
            elif msg_type == "parked_add":
                try:
                    task = parked_mod.add_task(
                        title=data.get("title", ""),
                        note=data.get("note", ""),
                        status=data.get("status", "parked"),
                        pane=data.get("pane"),
                        waiting_on=data.get("waiting_on", ""),
                        follow_up_at=data.get("follow_up_at"),
                        priority=data.get("priority", "normal"),
                    )
                    await _broadcast_parked_update(task, action="add")
                except ValueError as e:
                    await ws.send(json.dumps({"type": "parked_error",
                                              "error": str(e)}))
            elif msg_type == "parked_update":
                tid = data.get("id")
                if not isinstance(tid, int):
                    continue
                fields = {k: data[k] for k in
                          ("title", "note", "status", "pane",
                           "waiting_on", "follow_up_at", "priority")
                          if k in data}
                updated = parked_mod.update_task(tid, **fields)
                if updated:
                    await _broadcast_parked_update(updated, action="update")
            elif msg_type == "parked_delete":
                tid = data.get("id")
                if not isinstance(tid, int):
                    continue
                if parked_mod.delete_task(tid):
                    await _broadcast_parked_delete(tid)
            elif msg_type == "handoff_request":
                # Snapshot source pane's recent prose and inject it into target pane.
                source = data.get("source")
                target = data.get("target")
                instruction = (data.get("instruction") or "").strip()
                if (source is None or target is None
                        or not (0 <= source < NUM_SHELLS)
                        or not (0 <= target < NUM_SHELLS)
                        or source == target):
                    await ws.send(json.dumps({"type": "handoff_error",
                                              "error": "bad source/target"}))
                    continue
                prose = extract_recent_prose(source)
                if not prose:
                    await ws.send(json.dumps({"type": "handoff_error",
                                              "error": f"no prose to hand off from pane {source+1}"}))
                    continue
                wrapper = f"[Handoff from pane {source+1}]\n{prose}"
                if instruction:
                    wrapper += f"\n\n[Instruction] {instruction}"
                try:
                    os.write(masters[target], (wrapper + "\r").encode())
                    print(f"  Handoff: {source+1} -> {target+1} "
                          f"({len(prose)} chars prose)", flush=True)
                except (OSError, IndexError) as e:
                    await ws.send(json.dumps({"type": "handoff_error",
                                              "error": f"write failed: {e}"}))
                    continue
                if bus is not None:
                    bus._log({"type": "handoff", "source": source, "target": target,
                              "instruction": instruction, "ts": time.time()})
                await _broadcast_handoff(source, target, instruction)
            elif msg_type == "permission_response":
                # Resolve a pending permission request by sending y/n to the right pane.
                idx = data.get("shell")
                req_id = data.get("id")
                answer = data.get("answer")  # "allow" or "deny"
                if idx is None or not (0 <= idx < NUM_SHELLS):
                    continue
                if answer not in ("allow", "deny"):
                    continue
                key = "y" if answer == "allow" else "n"
                try:
                    os.write(masters[idx], key.encode())
                    print(f"  Perm: shell {idx} req {req_id} -> {answer}", flush=True)
                except (OSError, IndexError) as e:
                    print(f"  Perm write error shell {idx}: {e}", flush=True)
                    continue
                if bus is not None:
                    closed = bus.close_permission(idx, reason=answer)
                    if closed:
                        await _broadcast_permission_resolved(closed["id"], idx, answer)
            elif msg_type == "voice_start":
                voice_shell = data.get("shell", -1)
                print(f"  Voice active on pane {voice_shell + 1}", flush=True)
            elif msg_type == "voice_stop":
                voice_shell = -1
                voice_capturing.clear()
                voice_buffers.clear()
                print(f"  Voice stopped", flush=True)
            elif msg_type == "health_check":
                alive = []
                for i, pid in enumerate(child_pids):
                    try:
                        os.kill(pid, 0)
                        # Double-check with waitpid to catch zombies
                        result = os.waitpid(pid, os.WNOHANG)
                        alive.append(result[0] == 0)
                    except (OSError, ChildProcessError):
                        alive.append(False)
                await ws.send(json.dumps({"type": "health", "alive": alive}))
            elif msg_type == "exit_all":
                print("Exit all requested - saving session", flush=True)
                save_session()
                for fd in masters:
                    try:
                        os.write(fd, b"/exit\r")
                    except OSError:
                        pass
                await asyncio.sleep(2)
                # SIGTERM first
                for pid in child_pids:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except OSError:
                        pass
                await asyncio.sleep(1)
                # SIGKILL any survivors
                for pid in child_pids:
                    try:
                        os.kill(pid, signal.SIGKILL)
                    except OSError:
                        pass
                # Close all master FDs
                for fd in masters:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
                # Reap zombies
                for pid in child_pids:
                    try:
                        os.waitpid(pid, os.WNOHANG)
                    except (OSError, ChildProcessError):
                        pass
                os._exit(0)
            elif msg_type == "save_session":
                save_session()
                await ws.send(json.dumps({"type": "session_saved", "time": time.time()}))
            elif msg_type == "resize":
                cols = data.get("cols", 80)
                rows = data.get("rows", 24)
                # Validate resize values
                cols = max(10, min(500, int(cols)))
                rows = max(5, min(200, int(rows)))
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                for fd in masters:
                    try:
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
                    except OSError:
                        pass
    except websockets.ConnectionClosed:
        pass
    except Exception as e:
        print(f"  Handler error: {e}", flush=True)
    finally:
        clients.discard(ws)


async def serve(port):
    global loop
    loop = asyncio.get_running_loop()
    asyncio.create_task(autosave_loop())
    asyncio.create_task(bus_tick_loop())
    asyncio.create_task(cost_poll_loop())
    print(f"QuadMux UI:  http://localhost:{port}", flush=True)
    print(f"WebSocket:   ws://localhost:{port}", flush=True)
    async with websockets.serve(handler, "localhost", port, process_request=http_handler,
                                max_size=80 * 1024 * 1024):
        await asyncio.Future()


def graceful_shutdown():
    """Clean shutdown: save session, terminate children, close FDs, reap zombies."""
    save_session()
    # Mark this session's archive as ended.
    if session_dir:
        try:
            sessions_mod.update_meta(session_dir, ended_at=time.time())
        except OSError:
            pass
    # SIGTERM children
    for pid in child_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    # Give children 2s to exit
    deadline = time.time() + 2.0
    remaining = list(child_pids)
    while remaining and time.time() < deadline:
        time.sleep(0.1)
        still_alive = []
        for pid in remaining:
            try:
                result = os.waitpid(pid, os.WNOHANG)
                if result[0] == 0:
                    still_alive.append(pid)
            except (OSError, ChildProcessError):
                pass
        remaining = still_alive
    # SIGKILL any that didn't exit
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    # Final reap
    for pid in child_pids:
        try:
            os.waitpid(pid, os.WNOHANG)
        except (OSError, ChildProcessError):
            pass
    # Close all master FDs
    for fd in masters:
        try:
            os.close(fd)
        except OSError:
            pass
    # Prune worktrees created for this session
    if worktree_repo and pane_meta:
        for meta in pane_meta:
            path = meta.get("cwd")
            branch = meta.get("branch")
            if path and path.startswith(worktree_mod.WORKTREES_ROOT):
                worktree_mod.prune_worktree(worktree_repo, path)
                if branch and branch.startswith("qm/"):
                    worktree_mod.prune_branch(worktree_repo, branch)


def main():
    global NUM_SHELLS, shell_buffers, bus, pane_meta, worktree_repo, worktree_session_id
    global session_id, session_dir, session_started_at

    parser = argparse.ArgumentParser(description="QuadMux - Multi-pane Claude Code multiplexer")
    parser.add_argument("--shells", type=int, default=4, help="Number of Claude instances (default: 4)")
    parser.add_argument("--port", type=int, default=8766, help="WebSocket/HTTP port (default: 8766; 8765 is used by voicemode MCP)")
    parser.add_argument("--preset", type=str, default=None,
                        help=f"Role preset (one of: {', '.join(presets_mod.list_preset_names())}). "
                             "Each role becomes one pane with its own system prompt.")
    parser.add_argument("--repo", type=str, default=None,
                        help="Path to a git repo. With --preset, each pane gets a worktree off this repo.")
    parser.add_argument("--no-worktrees", action="store_true",
                        help="With --preset, skip git worktrees and run all panes in --repo (or cwd).")
    args = parser.parse_args()

    # Resolve preset + worktrees first so NUM_SHELLS matches the preset size.
    preset_roles = None
    if args.preset:
        preset_roles = presets_mod.get_preset(args.preset)
        if preset_roles is None:
            print(f"Error: unknown preset '{args.preset}'. "
                  f"Available: {', '.join(presets_mod.list_preset_names())}", flush=True)
            sys.exit(2)
        NUM_SHELLS = len(preset_roles)
        if args.shells != 4 and args.shells != NUM_SHELLS:
            print(f"  Note: --shells={args.shells} overridden by preset size ({NUM_SHELLS})", flush=True)
    else:
        NUM_SHELLS = args.shells

    pane_meta = [{} for _ in range(NUM_SHELLS)]

    if preset_roles:
        repo = args.repo or os.getcwd()
        repo = os.path.abspath(os.path.expanduser(repo))
        if args.no_worktrees:
            for i, role in enumerate(preset_roles):
                pane_meta[i] = {"role": role["name"], "cwd": repo,
                                "branch": worktree_mod.current_branch(repo) or ""}
        elif worktree_mod.is_git_repo(repo):
            worktree_repo = repo
            worktree_session_id = worktree_mod.make_session_id()
            print(f"Creating {NUM_SHELLS} worktrees off {repo} "
                  f"(session {worktree_session_id})...", flush=True)
            created = worktree_mod.setup_session_worktrees(
                repo, worktree_session_id, [r["name"] for r in preset_roles]
            )
            if len(created) != NUM_SHELLS:
                print(f"  Worktree creation incomplete ({len(created)}/{NUM_SHELLS}). "
                      "Falling back to running in --repo cwd.", flush=True)
                for i, role in enumerate(preset_roles):
                    pane_meta[i] = {"role": role["name"], "cwd": repo,
                                    "branch": worktree_mod.current_branch(repo) or ""}
                worktree_repo = None
            else:
                for i, (role, wt) in enumerate(zip(preset_roles, created)):
                    pane_meta[i] = {"role": role["name"], "cwd": wt["path"],
                                    "branch": wt["branch"]}
                    print(f"  Pane {i+1}: {role['name']} -> {wt['path']}", flush=True)
        else:
            print(f"  --repo {repo} is not a git repo. Running roles in that cwd without worktrees.", flush=True)
            for i, role in enumerate(preset_roles):
                pane_meta[i] = {"role": role["name"], "cwd": repo, "branch": ""}

        # Attach system prompts
        for i, role in enumerate(preset_roles):
            pane_meta[i]["system_prompt"] = role.get("system_prompt", "")

    # Phase 6: open a fresh archive dir for this run BEFORE loading buffers,
    # so load_session() picks the most recent *previous* session.
    session_id = sessions_mod.make_session_id()
    session_dir = sessions_mod.session_path(session_id)
    session_started_at = time.time()
    os.makedirs(session_dir, exist_ok=True)
    sessions_mod.write_meta(session_dir, {
        "started_at": session_started_at,
        "pane_count": NUM_SHELLS,
        "preset": args.preset,
        "repo": args.repo,
        "pane_meta": pane_meta,
    })

    # Load previous session buffers (conversation history survives restart)
    shell_buffers = load_session()
    bus = StatusBus(NUM_SHELLS)

    # Phase 5: initialise per-pane cost trackers. Session files are looked up
    # later (in cost_poll_loop) once Claude has actually created them.
    global cost_trackers
    cost_trackers = [costs_mod.CostTracker() for _ in range(NUM_SHELLS)]

    # 1. Find claude
    claude_path = find_claude()
    print(f"Using: {claude_path}", flush=True)

    # 2. Clear stale server on same port
    if not port_is_free(args.port):
        print(f"Port {args.port} in use - killing stale server...", flush=True)
        kill_stale_server(args.port)
        if not port_is_free(args.port):
            print(f"Error: port {args.port} still in use. Run: lsof -ti :{args.port} | xargs kill -9", flush=True)
            sys.exit(1)

    # 3. Spawn BEFORE asyncio starts (pty.fork + asyncio ordering matters)
    # Stagger spawns so the first pane completes OAuth token read/refresh
    # before the next starts. Avoids a refresh-token rotation race that
    # surfaces as "API error 401" on later panes.
    for i in range(NUM_SHELLS):
        if i > 0:
            time.sleep(1.5)
        meta = pane_meta[i] if i < len(pane_meta) else {}
        cwd = meta.get("cwd") or None
        sys_prompt = meta.get("system_prompt") or ""
        extra_args = ["--append-system-prompt", sys_prompt] if sys_prompt else []
        # Fixed session id so the cost tracker can find this pane's JSONL
        # deterministically (panes sharing a cwd are otherwise ambiguous).
        sid = str(uuid.uuid4())
        meta["session_id"] = sid
        extra_args += ["--session-id", sid]
        try:
            pid, master_fd = spawn_claude(claude_path, i, cwd=cwd, extra_args=extra_args)
        except RuntimeError as e:
            print(f"Error: {e}", flush=True)
            for prev_pid in child_pids:
                try:
                    os.kill(prev_pid, signal.SIGTERM)
                except OSError:
                    pass
            time.sleep(0.5)
            for prev_pid in child_pids:
                try:
                    os.kill(prev_pid, signal.SIGKILL)
                except OSError:
                    pass
            for prev_fd in masters:
                try:
                    os.close(prev_fd)
                except OSError:
                    pass
            # Reap zombies
            for prev_pid in child_pids:
                try:
                    os.waitpid(prev_pid, os.WNOHANG)
                except (OSError, ChildProcessError):
                    pass
            sys.exit(1)
        child_pids.append(pid)
        masters.append(master_fd)
        print(f"  Claude {i+1}: PID {pid}, fd={master_fd}", flush=True)

    # 4. Start reader threads
    for i, master_fd in enumerate(masters):
        t = threading.Thread(target=pty_reader_thread, args=(master_fd, i), daemon=True)
        t.start()

    # 5. Wait briefly, then verify all children still alive
    time.sleep(1)
    for i, pid in enumerate(child_pids):
        try:
            os.kill(pid, 0)
        except OSError:
            print(f"Warning: Claude {i+1} (PID {pid}) died during startup", flush=True)

    print(f"QuadMux: {NUM_SHELLS} Claude instances running", flush=True)
    asyncio.run(serve(args.port))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nShutting down... saving session", flush=True)
        graceful_shutdown()
