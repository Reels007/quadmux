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

masters = []
child_pids = []
clients = set()
loop = None
shell_buffers = []
_save_lock = threading.Lock()
MAX_BUFFER = 200
NUM_SHELLS = 4
SESSION_DIR = os.path.join(os.path.expanduser("~"), ".quadmux", "sessions")
AUTOSAVE_INTERVAL = 30  # seconds

# --- Voice response extraction (server-side) ---
ANSI_RE = re.compile(r'\x1b(?:\[[0-9;]*[a-zA-Z]|\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-2AB]|[>=<78DEHM])')

voice_shell = -1           # which shell has voice active (-1 = none)
voice_capturing = {}       # shell -> bool, are we capturing output after voice input?
voice_buffers = {}         # shell -> str, accumulated raw output since voice input
voice_timers = {}          # shell -> asyncio.TimerHandle for settling timeout

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
    """Save shell buffers and pane metadata to disk."""
    with _save_lock:
        os.makedirs(SESSION_DIR, exist_ok=True)
        for idx in range(NUM_SHELLS):
            path = os.path.join(SESSION_DIR, f"shell_{idx}.json")
            tmp_path = path + ".tmp"
            try:
                with open(tmp_path, "w") as f:
                    # Snapshot the buffer to avoid mutation during serialization
                    buf_copy = list(shell_buffers[idx])
                    json.dump({"buffer": buf_copy, "saved_at": time.time()}, f)
                os.replace(tmp_path, path)
            except (OSError, TypeError) as e:
                print(f"  Save error shell {idx}: {e}", flush=True)
                # Clean up temp file on failure
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
        print(f"  Session saved ({NUM_SHELLS} shells)", flush=True)


def load_session():
    """Load previous shell buffers from disk if available."""
    loaded = [[] for _ in range(NUM_SHELLS)]
    if not os.path.isdir(SESSION_DIR):
        return loaded
    for idx in range(NUM_SHELLS):
        path = os.path.join(SESSION_DIR, f"shell_{idx}.json")
        if os.path.exists(path):
            try:
                with open(path) as f:
                    raw = f.read()
                if not raw.strip():
                    print(f"  Load warning shell {idx}: empty file, skipping", flush=True)
                    continue
                data = json.loads(raw)
                loaded[idx] = data.get("buffer", [])
                saved = data.get("saved_at", 0)
                age = time.time() - saved
                print(f"  Loaded shell {idx}: {len(loaded[idx])} chunks ({age:.0f}s old)", flush=True)
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


def spawn_claude(claude_path, idx, rows=24, cols=80):
    """Spawn a Claude Code instance via pty.fork(). Verifies child is alive."""
    pid, master_fd = pty.fork()
    if pid == 0:
        # Child process - ensure full PATH for shebang scripts (macOS .app has minimal PATH)
        path = os.environ.get("PATH", "")
        for extra in ["/opt/homebrew/bin", "/usr/local/bin", os.path.expanduser("~/.local/bin")]:
            if extra not in path:
                path = extra + ":" + path
        os.environ["PATH"] = path
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLORTERM"] = "truecolor"
        try:
            os.execv(claude_path, [claude_path])
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
    print(f"QuadMux UI:  http://localhost:{port}", flush=True)
    print(f"WebSocket:   ws://localhost:{port}", flush=True)
    async with websockets.serve(handler, "localhost", port, process_request=http_handler,
                                max_size=80 * 1024 * 1024):
        await asyncio.Future()


def graceful_shutdown():
    """Clean shutdown: save session, terminate children, close FDs, reap zombies."""
    save_session()
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


def main():
    global NUM_SHELLS, shell_buffers

    parser = argparse.ArgumentParser(description="QuadMux - Multi-pane Claude Code multiplexer")
    parser.add_argument("--shells", type=int, default=4, help="Number of Claude instances (default: 4)")
    parser.add_argument("--port", type=int, default=8766, help="WebSocket/HTTP port (default: 8766; 8765 is used by voicemode MCP)")
    args = parser.parse_args()

    NUM_SHELLS = args.shells
    # Load previous session buffers (conversation history survives restart)
    shell_buffers = load_session()

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
        try:
            pid, master_fd = spawn_claude(claude_path, i)
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
