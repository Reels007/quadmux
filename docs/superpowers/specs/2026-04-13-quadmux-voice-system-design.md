# QuadMux Voice System - Design

**Status:** Approved 2026-04-13
**Author:** Sean Reel + Claude
**Supersedes:** existing voice code (commits `c61ed18`, `3f75368`)

## Problem

Prior voice implementations in QuadMux have been unreliable. All of the following failure modes have occurred:

1. Mic wouldn't activate / permission issues.
2. Ambient noise (music, thumping, keyboard clicks) transcribed as commands.
3. High latency between speech and text.
4. TTS either silent or spoke raw terminal output (ANSI, prompts).
5. No voice selection - JARVIS not usable.
6. Required local services (Whisper, Kokoro) crashed silently.
7. No keyword / hands-free activation (click-each-time only).

The existing system scrapes raw terminal output to decide what to speak, runs local Whisper with no hallucination guardrails, and fails silently when services are down. This design replaces the voice stack end-to-end.

## Goals

- **Bulletproof:** zero silent failures; every error surfaces to the user.
- **Fast:** first-word latency under 1.5s for STT; TTS streams as response text arrives.
- **Natural conversation:** click mic, have a multi-turn conversation, click again to end.
- **Voice choice:** user picks from ElevenLabs premium voices (including JARVIS/Daniel), Kokoro local voices, or browser system voices.
- **Graceful degradation:** if ElevenLabs is down, fall back to local Whisper / Kokoro / browser TTS with a visible banner.

## Non-goals

- Wake-word / hands-free activation. (Can be added later via Picovoice Porcupine.)
- Acoustic echo cancellation. (Added later if TTS feedback loop proves to be a problem in practice - assumes headphones for now.)
- Per-pane voice personalization. (One global voice, one global session at a time.)
- Mobile browser support.

## Locked decisions

| # | Decision | Rationale |
|---|----------|-----------|
| 1 | STT = **ElevenLabs Scribe** (cloud) | Uses existing API key; eliminates local Whisper fragility. |
| 2 | Activation = **click mic only** (no wake word) | Simplest; zero false triggers. |
| 3 | Turn end = **1.2 s silence (VAD)** | Natural conversation feel. |
| 4 | Voice picker = **3 tiers** (ElevenLabs, Kokoro, Browser) | Premium by default, graceful fallback. |
| 5 | Scope = **global voice session, global voice pick** | One active pane at a time; one voice everywhere. |
| 6 | Response extraction = **session transcript tailing** (`~/.claude/projects/.../session.jsonl`) | Clean assistant text without terminal scraping or changing shell invocation. |

## Architecture

Three layers:

```
┌──────────────────────────────────────────────────┐
│  BROWSER (quadmux.html)                           │
│  - Mic capture (getUserMedia + MediaRecorder)     │
│  - Web Audio VAD (silence detection)              │
│  - Voice settings UI, health indicator            │
│  - TTS audio playback (<audio> element)           │
└─────────────────┬────────────────────────────────┘
                  │  WebSocket + fetch
┌─────────────────▼────────────────────────────────┐
│  QUADMUX-SERVER (Python)                          │
│  - /api/stt   → proxies audio to ElevenLabs       │
│  - /api/tts   → routes ElevenLabs/Kokoro/noop     │
│  - /api/voices, /api/health/voice                 │
│  - Transcript watcher (session.jsonl diff → WS)   │
└─────────────────┬────────────────────────────────┘
                  │
┌─────────────────▼────────────────────────────────┐
│  EXTERNAL                                          │
│  - ElevenLabs API (STT + TTS, primary)            │
│  - Kokoro local service (TTS fallback)            │
│  - Local Whisper service (STT fallback)           │
│  - Browser SpeechSynthesis (TTS last resort)      │
└──────────────────────────────────────────────────┘
```

## Components

### 1. Browser: `voice.js` (new file, ~400 lines)

Replaces the existing inline voice block in `quadmux.html` (currently ~lines 1210-1400).

Responsibilities:
- Request mic permission; enumerate devices; remember last device in `localStorage`.
- Record audio via MediaRecorder (WebM/Opus).
- Run Web Audio VAD in a worklet: RMS threshold + hangover timer. Default 1.2 s of sub-threshold frames = end-of-turn.
- POST each utterance (one turn) to `/api/stt` as multipart form data.
- On transcript response: inject the text into the focused pane's xterm and send `\r`.
- Listen for WebSocket `voice_response` messages; POST the text to `/api/tts`; pipe the audio stream into an `<audio>` element.
- Render mic-button state: idle / listening (pulse) / capturing speech (brighter pulse) / transcribing (spinner) / speaking (waveform).

State machine:

```
idle ──click──▶ listening ──speech detected──▶ capturing
                    ▲                                │
                    │                          (1.2s silence)
                    │                                ▼
               speaking ◀─audio─ server ◀─text─ transcribing
                                                     │
                              (assistant response appends to session.jsonl)
```

Click mic a second time → immediate transition to `idle` from any state, cleanup all streams.

### 2. Server: `voice_api.py` (new module, mounted on quadmux-server)

Endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/stt` | Audio in (multipart `audio/webm`), transcript JSON out. Tries ElevenLabs first; falls back to local Whisper on 4xx/5xx/timeout. |
| `POST` | `/api/tts` | Body: `{text, voice_id, provider}`. Streams audio bytes back. |
| `GET` | `/api/voices` | Returns `{elevenlabs: [...], kokoro: [...], browser: null}`. ElevenLabs list is cached for 1 hour. Browser voices are enumerated client-side - this endpoint returns `null` for that tier as a sentinel. |
| `GET` | `/api/health/voice` | Probes each provider; returns `{elevenlabs, local_whisper, kokoro}` each as `ok \| down \| unconfigured`. |
| `WS`   | existing   | New message types: `voice_response`, `voice_health`, `voice_error`. |

Environment:
- `ELEVENLABS_API_KEY` - read from process env (already set in `~/.zshrc`).
- `KOKORO_URL` - default `http://localhost:8880`.
- `WHISPER_URL` - default `http://localhost:2022` (fallback only).

### 3. Server: transcript watcher (new, inside quadmux-server)

When voice is active for pane `N`:
1. Resolve the session's transcript path from Claude Code's session state (`~/.claude/projects/<encoded-cwd>/<session-id>.jsonl`).
2. Open the file and seek to EOF.
3. On each new line, parse JSON; if `type == "assistant"` and there's text content, emit a `voice_response` WebSocket message with the text delta.
4. Strip markdown (code fences, backticks, headers) before emitting - spoken code is noise.
5. Stop watching when voice is deactivated or the pane is closed.

### 4. Browser: voice settings modal (new)

Triggered by:
- Settings gear icon in header.
- Auto-shown on first-run if no voice preference saved.

Fields:
- Voice dropdown, grouped:
  - **ElevenLabs - Premium**: JARVIS (Daniel), Rachel, Adam, Nicole, Bella, Antoni
  - **Kokoro - Local free**: af_bella, af_nicole, am_adam, am_michael
  - **Browser - System voices**: enumerated via `speechSynthesis.getVoices()` at runtime
- Mic device dropdown (populated from `enumerateDevices()`).
- Health indicator row: 🟢 🟡 🔴 per service with "reconfigure" link for ElevenLabs if unconfigured.
- "Test voice" button - speaks a sample phrase.

Persisted in `localStorage` under keys `quadmux-voice-pick` and `quadmux-mic-device`.

## Data flow - one conversation turn

```
user clicks mic
  → browser: getUserMedia + MediaRecorder + VAD worklet start
  → mic button: "listening" pulse
user speaks
  → VAD detects speech onset → "capturing" state
user stops speaking
  → 1.2s silence detected → MediaRecorder.stop()
  → chunk POSTed to /api/stt (multipart)
  → server → ElevenLabs Scribe → transcript
  → response to browser
  → browser injects transcript into focused pane's xterm, sends Enter
  → Claude Code starts streaming response into session.jsonl
  → server's transcript watcher detects new assistant text
  → server emits voice_response over WebSocket
  → browser POSTs text chunk to /api/tts
  → server → ElevenLabs TTS streaming → audio bytes
  → browser pipes into <audio>, plays immediately
  → VAD restarts listening once audio is queued (not blocked by playback)
user clicks mic again → full teardown, idle
```

## Failure handling

Principle: **no silent failures**. Every failure produces a visible banner and a console log. The mic health dot reflects the current state.

| Failure | Behavior |
|---------|----------|
| ElevenLabs returns 401 | Banner: "ElevenLabs key invalid - falling back to local Whisper/Kokoro." Health: 🟡. Settings modal opened to the key field. |
| ElevenLabs timeout (>8 s) or 5xx | Retry once. Then fall back to local Whisper (STT) / Kokoro (TTS). Banner: "ElevenLabs slow - using local fallback." |
| Local Whisper not running (fallback requested) | Banner: "Fallback STT unavailable. Click to start Whisper service or reconfigure ElevenLabs." Voice session ends. |
| Kokoro not running | Skip to browser `speechSynthesis`. Banner: "Using system voice - Kokoro unavailable." |
| All TTS tiers fail | Banner: "No TTS available. Text responses only." Voice responses print to a toast instead of speaking. |
| Mic permission denied | Modal with platform-specific instructions and a deep link (Chrome: `chrome://settings/content/microphone`). |
| Mic device disappears (unplug) | Banner: "Mic disconnected." Voice session ends. |
| VAD detects no speech for 30 s after a turn | Chime, banner: "Didn't catch anything - click mic to resume." Session ends. |
| Transcript session file not found | Banner: "Can't find Claude Code session file. Make sure `claude` is running in this pane." |
| WebSocket disconnects during active voice | Voice session ends immediately; banner on reconnect. |

## Testing plan

**Unit (server, pytest):**
- `/api/stt` → mocked ElevenLabs 200, 401, 500, timeout. Assert correct fallback behavior and error messages.
- `/api/tts` → mocked ElevenLabs and Kokoro across the same failure grid.
- Transcript watcher: feed it a synthetic jsonl file with mixed assistant/tool/user messages; assert only assistant text emits.
- Markdown stripper: before/after pairs covering code fences, headers, inline code, lists.

**Integration (server, pytest + httpx):**
- End-to-end one-turn flow with all external calls mocked.
- Service health endpoint with each service selectively available.

**Manual browser tests:**
- 10-minute conversation on each of the 3 TTS tiers.
- Failure injection: revoke ElevenLabs key mid-conversation, kill Kokoro, unplug mic. Verify banners and fallback behavior.
- VAD tuning: test against music playing in room, typing, normal conversation - confirm no false triggers during the "listening" idle state (only during active click-to-start).
- Cross-browser: Chrome (primary), Safari (secondary). Firefox support is stretch.

## What gets ripped out

From `quadmux.html`:
- Inline voice code (≈ lines 1210-1400).
- Mic picker modal (replaced by settings modal).
- `voice-active` / `voice-hearing` animations (replaced with new state-machine-driven classes).

From `quadmux-server.py`:
- `voice_output_settled` and the prose-extraction regex stack.
- `voice_buffers`, `voice_capturing`, `voice_timers` globals.
- The terminal-output-scraping branch in the output handler.

Total lines removed: ≈ 300.

## What stays

- 4-pane layout and xterm rendering.
- Mic button in each pane header (re-wired to new state machine).
- WebSocket infrastructure (new message types added, existing untouched).
- Theme system (light/dark auto by Bermuda time).
- Session persistence.

## Open questions (deferred, not blocking)

- Do we want a "push-to-talk" hotkey as an alternative activation mode? (Deferred - click-only is locked for v1.)
- Should voice settings sync across devices? (Out of scope - local-only for now.)
- Is there a case for multiple simultaneous voice sessions across panes? (Explicitly ruled out per locked decision #5.)

## Rollout

1. Implement server module with tests, land on a feature branch.
2. Implement browser voice module on same branch; integrate.
3. Manual browser testing across failure matrix.
4. Merge to main behind a feature flag (`localStorage.quadmux-voice-v2 = "1"`) for a week.
5. Remove old voice code and flag; voice-v2 becomes the only path.
