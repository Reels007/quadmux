#!/usr/bin/env python3
"""QuadMux Server - Run multiple Claude Code instances via pty.fork() + xterm.js WebSocket.

Usage:
    python3 quadmux-server.py [--shells N] [--port PORT]

Requires: pip install websockets
"""

import argparse
import asyncio
import json
import os
import pty
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

try:
    import websockets
except ImportError:
    print("Installing websockets...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "websockets", "-q"])
    import websockets

masters = []
child_pids = []
clients = set()
loop = None
shell_buffers = []
MAX_BUFFER = 200
NUM_SHELLS = 4
SESSION_DIR = os.path.join(os.path.expanduser("~"), ".quadmux", "sessions")
AUTOSAVE_INTERVAL = 30  # seconds


def save_session():
    """Save shell buffers and pane metadata to disk."""
    os.makedirs(SESSION_DIR, exist_ok=True)
    for idx in range(NUM_SHELLS):
        path = os.path.join(SESSION_DIR, f"shell_{idx}.json")
        try:
            with open(path, "w") as f:
                json.dump({"buffer": shell_buffers[idx], "saved_at": time.time()}, f)
        except (OSError, TypeError) as e:
            print(f"  Save error shell {idx}: {e}", flush=True)
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
                    data = json.load(f)
                loaded[idx] = data.get("buffer", [])
                saved = data.get("saved_at", 0)
                age = time.time() - saved
                print(f"  Loaded shell {idx}: {len(loaded[idx])} chunks ({age:.0f}s old)", flush=True)
            except (json.JSONDecodeError, OSError) as e:
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
            time.sleep(0.5)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass


def port_is_free(port):
    """Check if a port is available."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
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
        # Child process
        os.environ["TERM"] = "xterm-256color"
        os.environ["COLORTERM"] = "truecolor"
        os.execv(claude_path, [claude_path])
    else:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        try:
            fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
        except OSError:
            pass
        # Verify child didn't die immediately (bad path, missing deps, etc.)
        time.sleep(0.3)
        result = os.waitpid(pid, os.WNOHANG)
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
            r, _, _ = select.select([master_fd], [], [], 1.0)
            if r:
                try:
                    data = os.read(master_fd, 16384)
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
    for ws in clients.copy():
        try:
            await ws.send(msg)
        except websockets.ConnectionClosed:
            clients.discard(ws)


def get_html_content():
    """Load the HTML frontend."""
    html_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quadmux.html")
    if os.path.exists(html_path):
        with open(html_path) as f:
            return f.read()
    return "<html><body><h1>quadmux.html not found</h1></body></html>"


async def http_handler(path, request_headers):
    """Serve the HTML UI on HTTP requests (non-WebSocket)."""
    if "Upgrade" not in request_headers:
        return (200, [("Content-Type", "text/html")], get_html_content().encode())
    return None


async def handler(ws):
    clients.add(ws)
    print(f"Client connected ({len(clients)} total)", flush=True)
    # Replay buffered output
    for idx in range(NUM_SHELLS):
        for chunk in shell_buffers[idx]:
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
            data = json.loads(message)
            if data["type"] == "input":
                idx = data["shell"]
                text = data["text"]
                raw = data.get("raw", False)
                if 0 <= idx < NUM_SHELLS:
                    try:
                        payload = text if raw else text + "\r"
                        os.write(masters[idx], payload.encode())
                    except OSError as e:
                        print(f"  Write error shell {idx}: {e}", flush=True)
            elif data["type"] == "health_check":
                alive = []
                for i, pid in enumerate(child_pids):
                    try:
                        os.kill(pid, 0)
                        alive.append(True)
                    except OSError:
                        alive.append(False)
                await ws.send(json.dumps({"type": "health", "alive": alive}))
            elif data["type"] == "exit_all":
                print("Exit all requested - saving session", flush=True)
                save_session()
                for fd in masters:
                    try:
                        os.write(fd, b"/exit\r")
                    except OSError:
                        pass
                await asyncio.sleep(2)
                for pid in child_pids:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except OSError:
                        pass
                await asyncio.sleep(1)
                os._exit(0)
            elif data["type"] == "save_session":
                save_session()
                await ws.send(json.dumps({"type": "session_saved", "time": time.time()}))
            elif data["type"] == "resize":
                cols = data.get("cols", 80)
                rows = data.get("rows", 24)
                winsize = struct.pack("HHHH", rows, cols, 0, 0)
                for fd in masters:
                    try:
                        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
                    except OSError:
                        pass
    except websockets.ConnectionClosed:
        pass
    finally:
        clients.discard(ws)


async def serve(port):
    global loop
    loop = asyncio.get_running_loop()
    asyncio.create_task(autosave_loop())
    print(f"QuadMux UI:  http://localhost:{port}", flush=True)
    print(f"WebSocket:   ws://localhost:{port}", flush=True)
    async with websockets.serve(handler, "localhost", port, process_request=http_handler):
        await asyncio.Future()


def main():
    global NUM_SHELLS, shell_buffers

    parser = argparse.ArgumentParser(description="QuadMux - Multi-pane Claude Code multiplexer")
    parser.add_argument("--shells", type=int, default=4, help="Number of Claude instances (default: 4)")
    parser.add_argument("--port", type=int, default=8765, help="WebSocket/HTTP port (default: 8765)")
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
        time.sleep(0.5)
        if not port_is_free(args.port):
            print(f"Error: port {args.port} still in use. Run: lsof -ti :{args.port} | xargs kill", flush=True)
            sys.exit(1)

    # 3. Spawn BEFORE asyncio starts (pty.fork + asyncio ordering matters)
    for i in range(NUM_SHELLS):
        try:
            pid, master_fd = spawn_claude(claude_path, i)
        except RuntimeError as e:
            print(f"Error: {e}", flush=True)
            for prev_pid in child_pids:
                try:
                    os.kill(prev_pid, signal.SIGTERM)
                except OSError:
                    pass
            for prev_fd in masters:
                try:
                    os.close(prev_fd)
                except OSError:
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
        save_session()
        for pid in child_pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        for fd in masters:
            try:
                os.close(fd)
            except OSError:
                pass
