# QuadMux "Glass Cockpit" UX/UI Redesign

**Date:** 2026-03-30
**Status:** Design approved
**Approach:** Glass Cockpit -- Warp-inspired frosted glass aesthetic, single-file architecture retained

## Context

QuadMux is a personal power-user tool for running 4 Claude Code instances in a browser-based 2x2 terminal multiplexer. It works well functionally but has a "hacker tool" aesthetic. Goal: transform it into a tier-1 dev tool that feels like Warp/iTerm2/Arc -- premium, polished, keyboard-first.

**Constraints:**
- Single HTML file architecture (simplicity is a feature)
- Personal tool -- optimize for information density and speed, not onboarding
- No build tooling or frameworks

## Section 1: Visual Foundation

### Color System
- Background: deep navy-black gradient (`#0a0e17` to `#0d1321`) replacing flat `#0a0a0a`
- Surface layers: semi-transparent whites
  - Cards/panes: `rgba(255,255,255,0.03)`
  - Headers: `rgba(255,255,255,0.06)`
  - Hover states: `rgba(255,255,255,0.10)`
- Pane accent colors (kept, refined):
  - Pane 1: `#4ade80` (green)
  - Pane 2: `#60a5fa` (blue)
  - Pane 3: `#facc15` (yellow)
  - Pane 4: `#c084fc` (purple)
- Text: `#e2e8f0` primary, `#94a3b8` secondary (slate palette)

### Glass Effects
- Pane headers: `backdrop-filter: blur(12px)`, background `rgba(255,255,255,0.06)`
- Input bar: `backdrop-filter: blur(16px)`, border `rgba(255,255,255,0.08)`
- Gutters: nearly invisible at rest, glow on hover

### Typography
- UI elements: `-apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif`
- Terminals: `'SF Mono', 'Fira Code', 'Consolas', monospace` (unchanged)
- Pane titles: 13px, weight 500
- Tighter letter-spacing on buttons

### Borders & Radius
- Pane corners: `10px` (up from 8px)
- Active pane: soft glow shadow `0 0 20px rgba(accent, 0.15)` replacing hard 2px border
- Subtle borders: `1px solid rgba(255,255,255,0.06)`

## Section 2: Pane Headers

### Structure
- Frosted glass bar with blur(12px) background
- Number badge: colored rounded square with subtle inner shadow for depth
- Title: weight 500, underline-style edit indicator on hover (no bordered box)

### Right Side (reorganized)
- **Health indicator:** colored line along top edge of header (accent at 30% alive, red pulse when dead)
- **Timer:** monospace pill with `rgba(accent, 0.1)` background when running
- **Focus indicator:** small glowing dot next to title (replaces "focused" text)
- **MIC button:** icon-based (mic Unicode/SVG) with glass hover style

### States
- **Active pane:** accent gradient along header bottom edge (2px), soft outer glow on pane
- **Dead pane:** opacity 0.35, health line turns red with slow pulse animation

## Section 3: Input Bar (Command Palette)

### Layout
- Floating, detached from bottom: `margin: 0 auto 12px`, `max-width: 720px`, centered
- Rounded corners (`12px`), frosted glass, subtle shadow underneath

### Input Field
- Center stage, `15px` font, no visible border at rest
- Focus state: soft accent glow

### Target Preview (redesigned)
- Implemented as a positioned overlay element inside the input wrapper (not inside the native `<input>`)
- The `<input>` gets left padding to make room for the pill when a target is detected
- Pill element sits absolutely positioned over the padding area:
  - Typing `2` shows colored `2` pill
  - Typing `*` shows white `ALL` pill
- Visually appears as an inline tagged input (Slack channel selector style) while keeping native input behavior

### Buttons (icon row, right side)
- Layout toggle: grid icon, morphs between states
- Clear: eraser icon
- Exit: power icon, red
- All icons 18px, ghost style (transparent bg, hover reveals glass pill)

### Status & Hints
- Mode hint: tiny contextual label above/below bar, fades after 2s
  - `Claude 1 focused` in accent color, or `command mode` in muted gray
- Connection status: small dot far right, green pulse (connected) / red (disconnected), no text
- Empty placeholder: `Type 1-4 to target, * for all, ? for help`

## Section 4: Interactions & Animations

### Focus Transitions
- Click pane: glow fades in `200ms ease-out`, previous fades out simultaneously
- Esc to bar: pane glow fades, input bar gets brief soft pulse

### Command Sent Feedback
- Target pane header flashes accent color (`300ms`)
- Input pill does quick scale-up/fade before clearing

### Layout Transitions
- CSS `transition` on grid template (`300ms ease`) -- smooth resize, not snap
- Terminals re-fit after transition completes

### Health State Changes
- Alive to dead: header line fades accent to red over `500ms`, opacity drops smoothly
- Dead to alive: green pulse animation plays once

### Auto-Approve Visibility
- Safe auto-approve: tiny `auto-approved` toast in pane header, fades after `1.5s`
- Security-flagged: pane border pulses red + `REVIEW` badge in header (persists until interaction)

### Gutter Hover
- Rest: `rgba(255,255,255,0.02)`, nearly invisible
- Hover: widen `4px` to `6px`, glow `rgba(255,255,255,0.15)`, cursor change

### Scrollbar Styling
- 6px thin, rounded, semi-transparent track
- Thumb: pane accent color at low opacity

## Section 5: Keyboard Shortcuts Overlay

### Modal Design
- Frosted glass, `border-radius: 16px`
- Entrance: scale `0.95` to `1` over `200ms`
- Backdrop: `rgba(0,0,0,0.6)` with `backdrop-filter: blur(8px)` (panes faintly visible)

### Layout
- Two-column grid, grouped by category:
  - **Navigation:** Esc, Ctrl+1-4
  - **Actions:** Ctrl+F, Ctrl+L, Ctrl+Shift+L
  - **Input bar:** prefix syntax, raw keypress rules

### Key Badges
- Rounded glass pills with subtle border
- Modifier keys accent-colored (Ctrl in blue, numbers in pane colors)

### Close Behavior
- Click backdrop or Esc (no explicit close button)
- Exit: fade + scale down `200ms`

## What Does NOT Change

- Python server (`quadmux-server.py`) -- untouched
- WebSocket protocol and message format -- untouched
- Core terminal functionality (xterm.js, pty routing) -- untouched
- All keyboard shortcuts and input bar prefix syntax -- behavior unchanged
- localStorage title persistence -- unchanged
- Auto-approve logic -- behavior unchanged (just made visible)
- Health check polling -- unchanged
