# QuadMux v2 Upgrade Plan

Snapshot baseline: tag `v1-may-2026` (commit `9eb6a6e`, pushed to origin).

## North Star
QuadMux v1 is a 2x2 terminal grid. v2 is a **multi-agent control room**: four (or more) Claude Code panes that know what each other are doing, hand off work between themselves, and surface a single permission/notification queue to the human. Same lightweight stack (Python pty + xterm.js), no Electron, no IDE wrapper.

## Design Principles
1. Keep the single-file frontend and `pty.fork()` server. No framework rewrite.
2. Each new feature must work with zero config for the default 2x2 layout.
3. The shared status bus is the spine. Everything plugs into it.
4. Nothing leaves localhost unless the user opts in.

---

## Phase 1: The Status Bus (highest leverage)

A small JSON event stream every pane writes to and reads from. Unlocks phases 2 and 3.

### Build
- **Status sidecar process** per pane: tails the pane PTY and emits structured events (`idle`, `thinking`, `awaiting_permission`, `tool_running`, `errored`, `done`) by pattern-matching Claude Code's output (re-use the ANSI cleaner already in `extract_prose`).
- **`~/.quadmux/bus.jsonl`** append-only event log + in-memory broadcaster.
- **WebSocket channel `/bus`** in `quadmux-server.py` that fans out events to the browser.
- **Per-pane badge** in the UI header: dot color + state label (idle/thinking/perm/error). Replaces today's binary green/red health dot.

### Touches
`quadmux-server.py` (new module `status_bus.py`), `quadmux.html` (badge + WS handler).

### Done when
All four panes show live state changes within 500ms of the underlying event.

---

## Phase 2: Permission Aggregator

A single floating tray that collects "Allow / Deny" prompts from every pane.

### Build
- Pattern-match the Claude Code permission prompt in the PTY stream (already partly done in `extract_prose`'s deny-list).
- When detected, raise a `awaiting_permission` event on the bus with the pane id + the question text.
- New UI panel (top-right floating, dismissible) lists pending prompts. Click "Allow" or "Deny" sends `y\n` / `n\n` to the right PTY.
- Optional: per-pane "auto-allow read tools" toggle.

### Touches
`quadmux-server.py` (prompt detector), `quadmux.html` (tray UI).

### Done when
Three panes asking for permission simultaneously all appear in one tray and resolve with one click each.

---

## Phase 3: Role Presets + Worktree-per-Pane

Spawn panes with roles, each in its own git worktree, so they cannot stomp on each other.

### Build
- **Preset layouts** in a config file (`~/.quadmux/presets.json`):
  - `planner+2impl+reviewer` (default)
  - `parallel-bugfix` (4 implementers on 4 worktrees)
  - `review-loop` (writer + critic + tester + ship)
- On spawn, run `git worktree add` per pane into `~/.quadmux/worktrees/{session}/pane-{n}` and `cd` into it before launching Claude.
- Each role gets a different system prompt injected via `--append-system-prompt` (or a per-pane `CLAUDE.md`).
- UI: preset picker on the start screen, pane title shows role name + worktree branch.

### Touches
`quadmux-server.py` (`spawn_shell` rework), `quadmux.html` (preset picker), new `presets/` dir.

### Done when
"Start a parallel-bugfix session on Bravo repo" gives four panes each in their own worktree, each pointed at a different ticket from a list.

---

## Phase 4: Cross-Pane @mentions and Handoffs

Type `@2: review the diff in pane 1` and the right context lands in pane 2.

### Build
- New input-bar parser: `@N:` prefix opens a handoff dialog.
- Handoff = (a) snapshot pane N's last clean prose output (the existing `extract_prose` already does this well) and (b) inject it as the user message in the target pane.
- "Send diff" shortcut: pipes `git diff` from pane A's worktree to pane B's stdin with a wrapper prompt.
- Bus event `handoff` lets you see in the activity log who handed what to whom.

### Touches
`quadmux.html` (input bar grammar), `quadmux-server.py` (handoff endpoint).

### Done when
`@2: review what 1 just wrote` causes pane 2 to receive pane 1's last response as a user message, no copy/paste.

---

## Phase 5: Cost Meter + Activity Log

Persistent ledger of what each pane is doing and what it costs.

### Build
- Tail the Claude Code session JSON in `~/.claude/projects/.../*.jsonl` per pane to pull token counts and cost.
- Live counter in each pane header: `$0.42 / 12k tok`.
- Aggregated total in the global header.
- Activity log panel (toggleable): scrollable list of all bus events with timestamps. Right-click an event to jump to that pane at that moment (xterm.js scrollback search).

### Touches
`quadmux-server.py` (Claude session file tailer), `quadmux.html` (cost chips + log panel).

### Done when
Running a four-pane session for an hour gives accurate per-pane and total spend matching `/cost` inside Claude Code.

---

## Phase 6: Notification Routing + Session Replay

Polish layer.

### Build
- Bus events route to:
  - **Jarvis voice** only when the active/focused pane finishes (existing `voice_routes.py` already targets one shell at a time, just hook the bus).
  - **macOS notification** when a background pane needs permission or errors out.
  - **Bell** otherwise.
- **Session replay**: every bus event + PTY buffer is already persisted (`~/.quadmux/sessions`). Add a "Replay session" picker in the UI that lets you scrub the activity log of an old session and view the prose output. Useful for "what did pane 3 actually do last Tuesday".

### Touches
`quadmux-server.py` (notify hooks), `quadmux.html` (replay UI), `voice_routes.py` (subscribe to bus instead of polling).

### Done when
A pane finishing in the background gives one OS notification, the active pane finishing speaks via Jarvis, and yesterday's session is browsable.

---

## Phasing and Sequencing

| Phase | Effort | Depends on | Ship as |
|------:|:------:|:----------:|:-------:|
| 1 Status Bus            | M | -        | v2.0.0 |
| 2 Permission Aggregator | S | 1        | v2.1.0 |
| 3 Role Presets + Worktrees | M | 1     | v2.2.0 |
| 4 @mentions / Handoffs  | S | 1        | v2.3.0 |
| 5 Cost Meter + Activity Log | M | 1, 2 | v2.4.0 |
| 6 Notifications + Replay | S | 1, 5    | v2.5.0 |

Effort key: S = half-day, M = 1-2 days.

Phases 2, 3, 4 are independent after phase 1 ships, so they can be parallelised across QuadMux instances (eat your own dog food).

## Out of Scope (v2)
- Windows support (pty.fork is POSIX, accept it).
- Multi-user / remote access (still localhost only).
- Replacing xterm.js with a custom renderer.
- IDE integration. QuadMux stays browser-only.

## Risks
- **Pattern-matching Claude Code output is brittle.** It changes between CLI versions. Mitigation: keep all regexes in one module (`pty_patterns.py`), version-gate them, and fall back to "unknown" state cleanly.
- **Worktree churn.** Heavy disk use for big repos. Mitigation: prune-on-close, and a "worktree status" command in the input bar.
- **Bus log growth.** Cap `bus.jsonl` at 50 MB with rotation.

## First Action After Approval
Spike phase 1 in a `feat/v2-status-bus` branch off `v1-may-2026`: minimum status bus + per-pane state badge, no other features. Ship as v2.0.0-alpha and dogfood for a week before phase 2.
