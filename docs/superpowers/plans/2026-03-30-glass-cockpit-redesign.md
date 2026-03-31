# QuadMux "Glass Cockpit" Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform QuadMux's visual layer from hacker-tool aesthetic to Warp-inspired frosted glass premium dev tool, touching only `quadmux.html`.

**Architecture:** Pure CSS/HTML/JS redesign within the existing single-file architecture. No server changes. All glass effects via `backdrop-filter`, animations via CSS transitions, layout unchanged functionally. The input bar becomes a floating command palette with overlay-based target pills.

**Tech Stack:** HTML, CSS (backdrop-filter, custom properties, animations), vanilla JS, xterm.js (unchanged)

---

## File Structure

- **Modify:** `quadmux.html` -- all changes happen here (CSS, HTML structure, JS)
- **No new files created** -- single-file architecture retained

The plan is organized as incremental visual layers. Each task produces a working, visually improved state that can be committed independently.

---

### Task 1: Visual Foundation -- Color System & Glass Base

**Files:**
- Modify: `quadmux.html:9-308` (entire `<style>` block)

This task replaces the flat dark theme with the navy gradient background, glass surface variables, slate text palette, and updated border/radius values. No structural HTML changes.

- [ ] **Step 1: Verify current state loads**

Open `quadmux.html` directly in browser (no server needed for CSS check). Confirm it renders the current dark UI. This is our visual baseline.

Run: `open "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux/quadmux.html"`

- [ ] **Step 2: Replace the CSS reset and body styles with glass foundation**

Replace the `* { margin: 0; ... }` and `body { ... }` block at the top of `<style>` with:

```css
* { margin: 0; padding: 0; box-sizing: border-box; }

:root {
  --bg-base: #0a0e17;
  --bg-gradient: linear-gradient(145deg, #0a0e17 0%, #0d1321 100%);
  --surface-1: rgba(255,255,255,0.03);
  --surface-2: rgba(255,255,255,0.06);
  --surface-3: rgba(255,255,255,0.10);
  --border-subtle: rgba(255,255,255,0.06);
  --border-medium: rgba(255,255,255,0.10);
  --text-primary: #e2e8f0;
  --text-secondary: #94a3b8;
  --text-muted: #64748b;
  --glass-blur-sm: blur(8px);
  --glass-blur-md: blur(12px);
  --glass-blur-lg: blur(16px);
  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 12px;
  --radius-xl: 16px;
  --transition-fast: 150ms ease;
  --transition-normal: 200ms ease-out;
  --transition-slow: 300ms ease;
}

body {
  background: var(--bg-gradient);
  color: var(--text-primary);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
```

- [ ] **Step 3: Update grid and gutter styles**

Replace the `.grid`, `.gutter-col`, and `.gutter-row` styles:

```css
.grid {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 4px 1fr;
  grid-template-rows: 1fr 4px 1fr;
  padding: 6px;
  gap: 0;
  min-height: 0;
}

.gutter-col {
  grid-row: 1 / -1;
  cursor: col-resize;
  background: rgba(255,255,255,0.02);
  border-radius: 2px;
  transition: all var(--transition-normal);
}
.gutter-col:hover, .gutter-col.dragging {
  background: rgba(255,255,255,0.15);
  width: 6px;
  margin: 0 -1px;
}

.gutter-row {
  grid-column: 1 / -1;
  cursor: row-resize;
  background: rgba(255,255,255,0.02);
  border-radius: 2px;
  transition: all var(--transition-normal);
}
.gutter-row:hover, .gutter-row.dragging {
  background: rgba(255,255,255,0.15);
  height: 6px;
  margin: -1px 0;
}
```

- [ ] **Step 4: Update shell (pane) container styles**

Replace `.shell`, `.shell.active`, `.shell.flash`, `.shell.dead`:

```css
.shell {
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-md);
  overflow: hidden;
  border: 1px solid var(--border-subtle);
  background: var(--surface-1);
  transition: border-color var(--transition-normal), box-shadow var(--transition-normal), opacity var(--transition-slow);
  cursor: pointer;
  min-height: 0;
  min-width: 0;
}

.shell.active {
  border-color: color-mix(in srgb, var(--accent) 40%, transparent) !important;
  box-shadow: 0 0 20px color-mix(in srgb, var(--accent) 15%, transparent),
              inset 0 0 0 1px color-mix(in srgb, var(--accent) 10%, transparent);
}

.shell.flash {
  animation: flash 0.4s ease-out;
}

.shell.dead {
  opacity: 0.35;
  transition: opacity 500ms ease;
}
```

- [ ] **Step 5: Update accent colors and term-container**

Replace the `.s1`-`.s4` and `.term-container` styles:

```css
.s1 { --accent: #4ade80; }
.s2 { --accent: #60a5fa; }
.s3 { --accent: #facc15; }
.s4 { --accent: #c084fc; }

.term-container {
  flex: 1;
  min-height: 0;
  background: rgba(0,0,0,0.3);
  padding: 4px;
}
```

- [ ] **Step 6: Update flash animation**

Replace the `@keyframes flash`:

```css
@keyframes flash {
  0% {
    border-color: color-mix(in srgb, var(--accent) 50%, transparent);
    box-shadow: 0 0 20px color-mix(in srgb, var(--accent) 25%, transparent);
  }
  100% {
    border-color: var(--border-subtle);
    box-shadow: none;
  }
}
```

- [ ] **Step 7: Add scrollbar styling**

Add after the `@keyframes flash` block:

```css
/* --- Custom scrollbars --- */
.term-container ::-webkit-scrollbar { width: 6px; }
.term-container ::-webkit-scrollbar-track { background: transparent; }
.term-container ::-webkit-scrollbar-thumb {
  background: color-mix(in srgb, var(--accent) 25%, transparent);
  border-radius: 3px;
}
.term-container ::-webkit-scrollbar-thumb:hover {
  background: color-mix(in srgb, var(--accent) 40%, transparent);
}
```

- [ ] **Step 8: Verify visual foundation renders**

Open in browser. Confirm:
- Navy gradient background visible
- Panes have subtle glass borders
- Gutters are nearly invisible
- Text is slate-colored, not harsh white

Run: `open "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux/quadmux.html"`

- [ ] **Step 9: Commit**

```bash
cd "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux"
git add quadmux.html
git commit -m "feat: glass cockpit visual foundation -- gradient bg, glass surfaces, slate text"
```

---

### Task 2: Pane Headers -- Glass Headers with Health Line & Focus Dot

**Files:**
- Modify: `quadmux.html:66-131` (header CSS)
- Modify: `quadmux.html:312-395` (header HTML for all 4 panes)

- [ ] **Step 1: Replace shell-header CSS**

Replace `.shell-header` through `.shell-timer.running` (the entire header styles section) with:

```css
.shell-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 12px;
  background: var(--surface-2);
  backdrop-filter: var(--glass-blur-md);
  -webkit-backdrop-filter: var(--glass-blur-md);
  font-size: 13px;
  font-weight: 500;
  flex-shrink: 0;
  position: relative;
  border-bottom: 1px solid var(--border-subtle);
}

/* Health line along top of header */
.shell-header::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: color-mix(in srgb, var(--accent) 30%, transparent);
  transition: background var(--transition-slow);
}
.shell.dead .shell-header::before {
  background: #f87171;
  animation: health-pulse 2s ease-in-out infinite;
}

/* Active pane bottom accent line */
.shell.active .shell-header::after {
  content: '';
  position: absolute;
  bottom: 0;
  left: 0;
  right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
}

@keyframes health-pulse {
  0%, 100% { opacity: 0.4; }
  50% { opacity: 1; }
}

.shell-number {
  width: 22px;
  height: 22px;
  border-radius: 6px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  color: var(--bg-base);
  background: var(--accent);
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.2), 0 1px 3px rgba(0,0,0,0.3);
  flex-shrink: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}

.shell-title {
  color: var(--text-primary);
  cursor: text;
  padding: 1px 4px;
  border-radius: 3px;
  border-bottom: 1px solid transparent;
  min-width: 40px;
  outline: none;
  transition: border-color var(--transition-fast);
}
.shell-title:hover { border-bottom-color: var(--text-muted); }
.shell-title:focus { border-bottom-color: var(--accent); }

.shell-status {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--text-muted);
}

/* Health dot removed from HTML -- using header ::before line instead */
.shell-health { display: none; }

/* Focus dot replaces "focused" text */
.shell-focus-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--accent);
  box-shadow: 0 0 6px var(--accent);
  display: none;
}
.shell.active .shell-focus-dot { display: block; }

.shell-focus-hint { display: none; }

.shell-timer {
  font-size: 10px;
  color: var(--text-muted);
  font-variant-numeric: tabular-nums;
  font-family: 'SF Mono', 'Fira Code', monospace;
  min-width: 44px;
  text-align: right;
}
.shell-timer.running {
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  padding: 1px 6px;
  border-radius: 4px;
}

.btn-voice {
  background: var(--surface-2);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  cursor: pointer;
  padding: 2px 6px;
  font-size: 14px;
  line-height: 1;
  transition: all var(--transition-fast);
}
.btn-voice:hover {
  border-color: color-mix(in srgb, var(--accent) 50%, transparent);
  color: var(--accent);
  background: var(--surface-3);
}
.btn-voice.voice-active {
  border-color: var(--accent);
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 15%, transparent);
}
```

- [ ] **Step 2: Update shell-search CSS**

Replace `.shell-search` styles:

```css
.shell-search {
  display: none;
  padding: 6px 10px;
  background: var(--surface-2);
  backdrop-filter: var(--glass-blur-sm);
  -webkit-backdrop-filter: var(--glass-blur-sm);
  border-top: 1px solid var(--border-subtle);
}
.shell-search.open { display: flex; gap: 6px; align-items: center; }
.shell-search input {
  flex: 1;
  background: rgba(0,0,0,0.3);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  color: var(--text-primary);
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 12px;
  outline: none;
  transition: border-color var(--transition-fast);
}
.shell-search input:focus { border-color: var(--accent); }
.shell-search button {
  background: var(--surface-3);
  border: 1px solid var(--border-subtle);
  color: var(--text-secondary);
  border-radius: var(--radius-sm);
  padding: 4px 8px;
  font-size: 12px;
  cursor: pointer;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
  transition: all var(--transition-fast);
}
.shell-search button:hover { background: var(--surface-3); color: var(--text-primary); border-color: var(--border-medium); }
```

- [ ] **Step 3: Update pane header HTML for all 4 panes**

For each of the 4 shell divs (shell-0 through shell-3), update the header. Here is the pattern for shell-0 (pane index `0`, number `1`). Repeat for shells 1-3, changing the index and number accordingly:

```html
<div class="shell-header">
  <div class="shell-number">1</div>
  <div class="shell-title" contenteditable="true" spellcheck="false" data-shell="0">Claude 1</div>
  <div class="shell-status">
    <button class="btn-voice" id="voice-0" data-shell="0" title="Toggle voice mode">&#x1F399;</button>
    <span class="shell-focus-dot" id="focus-0"></span>
    <span class="shell-timer" id="timer-0"></span>
    <span class="shell-health" id="health-0"></span>
  </div>
</div>
```

Changes from original:
- MIC text replaced with mic emoji `&#x1F399;`
- `shell-focus-hint` span replaced with `shell-focus-dot` span
- `shell-health` span kept in HTML (for JS health check) but hidden via CSS

- [ ] **Step 4: Verify headers render**

Open in browser. Confirm:
- Glass headers with backdrop blur
- Colored health line along top edge of each pane
- Number badge has inner shadow depth
- Focus shows glowing dot, not "focused" text
- Mic button shows icon not text

- [ ] **Step 5: Commit**

```bash
cd "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux"
git add quadmux.html
git commit -m "feat: glass pane headers with health line, focus dot, icon mic button"
```

---

### Task 3: Floating Command Palette Input Bar

**Files:**
- Modify: `quadmux.html:190-273` (input bar CSS)
- Modify: `quadmux.html:397-412` (input bar HTML)
- Modify: `quadmux.html:796-861` (input bar JS -- preview logic)

- [ ] **Step 1: Replace input bar CSS**

Replace `.input-bar` through `.mode-hint` styles:

```css
/* --- Floating command palette --- */
.input-bar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 16px;
  margin: 0 auto 12px;
  max-width: 720px;
  width: calc(100% - 24px);
  background: var(--surface-2);
  backdrop-filter: var(--glass-blur-lg);
  -webkit-backdrop-filter: var(--glass-blur-lg);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-lg);
  box-shadow: 0 8px 32px rgba(0,0,0,0.3), 0 2px 8px rgba(0,0,0,0.2);
  flex-shrink: 0;
  position: relative;
}

.input-wrapper {
  flex: 1;
  position: relative;
  display: flex;
  align-items: center;
}

.input-pill {
  position: absolute;
  left: 10px;
  font-size: 12px;
  font-weight: 700;
  padding: 2px 8px;
  border-radius: 4px;
  color: var(--bg-base);
  z-index: 1;
  pointer-events: none;
  opacity: 0;
  transform: scale(0.8);
  transition: opacity var(--transition-fast), transform var(--transition-fast);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}
.input-pill.visible {
  opacity: 1;
  transform: scale(1);
}

#input {
  flex: 1;
  background: transparent;
  border: none;
  padding: 8px 12px;
  color: var(--text-primary);
  font-family: 'SF Mono', 'Fira Code', monospace;
  font-size: 15px;
  outline: none;
  transition: padding-left var(--transition-fast);
}
#input::placeholder { color: var(--text-muted); }
#input.has-target { padding-left: 48px; }

.bar-buttons {
  display: flex;
  gap: 2px;
  align-items: center;
}

.btn-icon {
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  padding: 6px 8px;
  cursor: pointer;
  color: var(--text-secondary);
  font-size: 16px;
  line-height: 1;
  transition: all var(--transition-fast);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}
.btn-icon:hover {
  background: var(--surface-3);
  border-color: var(--border-subtle);
  color: var(--text-primary);
}
.btn-icon.btn-exit {
  color: #f87171;
}
.btn-icon.btn-exit:hover {
  background: rgba(248,113,113,0.15);
  border-color: rgba(248,113,113,0.3);
  color: #f87171;
}

.mode-hint {
  position: absolute;
  top: -20px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 11px;
  color: var(--text-muted);
  white-space: nowrap;
  opacity: 0;
  transition: opacity var(--transition-normal);
  pointer-events: none;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}
.mode-hint.visible { opacity: 1; }

.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: #f87171;
  flex-shrink: 0;
  transition: background var(--transition-normal);
}
.status-dot.connected {
  background: #4ade80;
  box-shadow: 0 0 6px rgba(74,222,128,0.4);
  animation: status-pulse 3s ease-in-out infinite;
}

@keyframes status-pulse {
  0%, 100% { box-shadow: 0 0 6px rgba(74,222,128,0.2); }
  50% { box-shadow: 0 0 6px rgba(74,222,128,0.5); }
}
```

- [ ] **Step 2: Remove old preview and button CSS**

Delete the `.input-preview`, `.preview-target`, `.preview-arrow`, `.preview-cmd`, `.btn`, `.btn-exit`, `.btn-clear`, `.btn-layout`, and `.status` styles (they are all replaced above).

- [ ] **Step 3: Replace input bar HTML**

Replace the entire `.input-bar` div and its contents:

```html
<div class="input-bar">
  <span class="mode-hint" id="mode-hint"></span>
  <div class="input-wrapper">
    <div class="input-pill" id="input-pill"></div>
    <input type="text" id="input" placeholder="Type 1-4 to target, * for all, ? for help">
  </div>
  <div class="bar-buttons">
    <button class="btn-icon" id="btn-layout" title="Toggle layout">&#9638;</button>
    <button class="btn-icon" id="btn-clear" title="Clear all terminals">&#8999;</button>
    <button class="btn-icon btn-exit" id="btn-exit" title="Exit all instances">&#9211;</button>
  </div>
  <div class="status-dot" id="status-dot" title="disconnected"></div>
</div>
```

- [ ] **Step 4: Update the input preview JS**

Replace the `inputEl.addEventListener('input', ...)` handler and related preview code. Find this block in the JS:

```js
inputEl.addEventListener('input', () => {
  const val = inputEl.value;
  if (val.length > 0 && val[0] === '*') {
    previewTarget.style.background = '#fff';
    previewTarget.textContent = '*';
    previewCmd.textContent = val.slice(1) || '...';
  } else if (val.length > 0 && '1234'.includes(val[0])) {
    const idx = parseInt(val[0]) - 1;
    previewTarget.style.background = colors[idx];
    previewTarget.textContent = val[0];
    previewCmd.textContent = val.slice(1) || '...';
  } else {
    previewTarget.style.background = '#333';
    previewTarget.textContent = '-';
    previewCmd.textContent = val || '...';
  }
});
```

Replace with:

```js
const inputPill = document.getElementById('input-pill');

inputEl.addEventListener('input', () => {
  const val = inputEl.value;
  if (val.length > 0 && val[0] === '*') {
    inputPill.style.background = '#fff';
    inputPill.textContent = 'ALL';
    inputPill.classList.add('visible');
    inputEl.classList.add('has-target');
  } else if (val.length > 0 && '1234'.includes(val[0])) {
    const idx = parseInt(val[0]) - 1;
    inputPill.style.background = colors[idx];
    inputPill.textContent = val[0];
    inputPill.classList.add('visible');
    inputEl.classList.add('has-target');
  } else {
    inputPill.classList.remove('visible');
    inputEl.classList.remove('has-target');
  }
});
```

- [ ] **Step 5: Update the JS variable declarations for removed elements**

Find and remove these lines from the top of the `<script>` block:

```js
const previewTarget = document.getElementById('preview-target');
const previewCmd = document.getElementById('preview-cmd');
```

Also update `statusEl` references. Find:

```js
const statusEl = document.getElementById('status');
```

Replace with:

```js
const statusDot = document.getElementById('status-dot');
```

- [ ] **Step 6: Update WebSocket status references**

In the `connect()` function, find:

```js
ws.onopen = () => {
    statusEl.textContent = 'connected';
    statusEl.className = 'status connected';
```

Replace with:

```js
ws.onopen = () => {
    statusDot.classList.add('connected');
    statusDot.title = 'connected';
```

Find:

```js
ws.onclose = () => {
    statusEl.textContent = 'disconnected';
    statusEl.className = 'status disconnected';
```

Replace with:

```js
ws.onclose = () => {
    statusDot.classList.remove('connected');
    statusDot.title = 'disconnected';
```

- [ ] **Step 7: Update mode hint to show/fade**

In the `setActiveShell` function, find:

```js
if (idx >= 0) {
    terms[idx].focus();
    modeHint.textContent = `Claude ${idx + 1} focused`;
    modeHint.style.color = colors[idx];
  } else {
    focusLocked = false;
    inputEl.focus();
    modeHint.textContent = 'bar mode';
    modeHint.style.color = '#555';
  }
```

Replace with:

```js
if (idx >= 0) {
    terms[idx].focus();
    modeHint.textContent = `Claude ${idx + 1} focused`;
    modeHint.style.color = colors[idx];
    modeHint.classList.add('visible');
    clearTimeout(modeHint._fadeTimer);
    modeHint._fadeTimer = setTimeout(() => modeHint.classList.remove('visible'), 2000);
  } else {
    focusLocked = false;
    inputEl.focus();
    modeHint.textContent = 'command mode';
    modeHint.style.color = 'var(--text-muted)';
    modeHint.classList.add('visible');
    clearTimeout(modeHint._fadeTimer);
    modeHint._fadeTimer = setTimeout(() => modeHint.classList.remove('visible'), 2000);
  }
```

- [ ] **Step 8: Update input submit handler to clear pill**

In the `inputEl.addEventListener('keydown', ...)` handler, find the block at the end that clears the input:

```js
  inputEl.value = '';
  previewTarget.style.background = '#333';
  previewTarget.textContent = '-';
  previewCmd.textContent = '...';
```

Replace with:

```js
  inputEl.value = '';
  inputPill.classList.remove('visible');
  inputEl.classList.remove('has-target');
```

- [ ] **Step 9: Verify command palette renders and works**

Open in browser. Confirm:
- Floating glass bar centered at bottom with rounded corners and shadow
- Typing `2` shows colored pill inside input
- Typing `*` shows white ALL pill
- Icon buttons on right (grid, eraser, power)
- Connection dot in far right
- Mode hint appears above bar and fades

- [ ] **Step 10: Commit**

```bash
cd "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux"
git add quadmux.html
git commit -m "feat: floating command palette with inline target pills and icon buttons"
```

---

### Task 4: Auto-Approve Toast & Security Review Badge

**Files:**
- Modify: `quadmux.html` (CSS for toast/badge, JS for auto-approve visibility)

- [ ] **Step 1: Add toast and badge CSS**

Add after the `.shell-search button:hover` style:

```css
/* --- Auto-approve toast --- */
.shell-toast {
  position: absolute;
  top: 6px;
  right: 12px;
  font-size: 10px;
  color: var(--accent);
  background: color-mix(in srgb, var(--accent) 10%, transparent);
  border: 1px solid color-mix(in srgb, var(--accent) 20%, transparent);
  padding: 2px 8px;
  border-radius: 4px;
  opacity: 0;
  transform: translateY(-4px);
  transition: opacity var(--transition-normal), transform var(--transition-normal);
  pointer-events: none;
  z-index: 5;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}
.shell-toast.visible {
  opacity: 1;
  transform: translateY(0);
}

.shell-review-badge {
  font-size: 10px;
  font-weight: 700;
  color: #f87171;
  background: rgba(248,113,113,0.15);
  border: 1px solid rgba(248,113,113,0.3);
  padding: 1px 6px;
  border-radius: 4px;
  display: none;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}
.shell-review-badge.visible { display: inline-block; }
```

- [ ] **Step 2: Add toast and badge HTML to each pane header**

In each shell's `.shell-status` div, add before the `btn-voice` button:

```html
<span class="shell-review-badge" id="review-0">REVIEW</span>
<span class="shell-toast" id="toast-0">auto-approved</span>
```

(Use `review-1`, `toast-1`, etc. for shells 1-3.)

Also add `position: relative;` to `.shell-header` if not already present (it is from Task 2).

- [ ] **Step 3: Update auto-approve JS to show toast**

In the `ws.onmessage` handler, find the auto-approve section. The block that sends `'y'`:

```js
} else {
  // Safe - auto-approve
  ws.send(JSON.stringify({ type: 'input', shell: data.shell, text: 'y', raw: true }));
}
```

Replace with:

```js
} else {
  // Safe - auto-approve
  ws.send(JSON.stringify({ type: 'input', shell: data.shell, text: 'y', raw: true }));
  // Show toast
  const toast = document.getElementById(`toast-${data.shell}`);
  toast.classList.add('visible');
  setTimeout(() => toast.classList.remove('visible'), 1500);
}
```

- [ ] **Step 4: Update security risk JS to show review badge**

Find the security risk block:

```js
if (isSecurityRisk(cleanApproval)) {
  // Risky - focus pane, let user decide
  focusLocked = false;
  setActiveShell(data.shell);
  focusLocked = true;
  shellEls[data.shell].style.borderColor = '#f87171';
  setTimeout(() => { shellEls[data.shell].style.borderColor = ''; }, 2000);
```

Replace with:

```js
if (isSecurityRisk(cleanApproval)) {
  // Risky - focus pane, let user decide
  focusLocked = false;
  setActiveShell(data.shell);
  focusLocked = true;
  shellEls[data.shell].style.borderColor = '#f87171';
  setTimeout(() => { shellEls[data.shell].style.borderColor = ''; }, 2000);
  // Show review badge
  const badge = document.getElementById(`review-${data.shell}`);
  badge.classList.add('visible');
  // Clear badge when user interacts with this pane
  const clearBadge = () => {
    badge.classList.remove('visible');
    shellEls[data.shell].removeEventListener('click', clearBadge);
  };
  shellEls[data.shell].addEventListener('click', clearBadge);
```

- [ ] **Step 5: Verify toast and badge**

This is hard to test without running Claude instances. Visually verify the CSS renders by temporarily adding `visible` class in dev tools.

- [ ] **Step 6: Commit**

```bash
cd "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux"
git add quadmux.html
git commit -m "feat: auto-approve toast notification and security review badge"
```

---

### Task 5: Keyboard Shortcuts Overlay -- Glass Modal

**Files:**
- Modify: `quadmux.html` (shortcuts CSS + HTML)

- [ ] **Step 1: Replace shortcuts overlay CSS**

Replace `.shortcuts-overlay` through `.shortcuts-box kbd`:

```css
/* --- Keyboard shortcuts overlay --- */
.shortcuts-overlay {
  display: none;
  position: fixed;
  top: 0; left: 0; right: 0; bottom: 0;
  background: rgba(0,0,0,0.6);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  z-index: 100;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity var(--transition-normal);
}
.shortcuts-overlay.open {
  display: flex;
  opacity: 1;
}

.shortcuts-box {
  background: var(--surface-2);
  backdrop-filter: var(--glass-blur-lg);
  -webkit-backdrop-filter: var(--glass-blur-lg);
  border: 1px solid var(--border-medium);
  border-radius: var(--radius-xl);
  padding: 28px 36px;
  max-width: 520px;
  width: 90%;
  font-size: 13px;
  line-height: 2;
  color: var(--text-primary);
  box-shadow: 0 16px 64px rgba(0,0,0,0.4);
  transform: scale(0.95);
  transition: transform var(--transition-normal);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
}
.shortcuts-overlay.open .shortcuts-box {
  transform: scale(1);
}

.shortcuts-box h3 {
  margin-bottom: 16px;
  color: var(--text-primary);
  font-size: 16px;
  font-weight: 600;
}

.shortcuts-section {
  margin-bottom: 16px;
}
.shortcuts-section-title {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--text-muted);
  margin-bottom: 4px;
  font-weight: 600;
}

.shortcuts-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 24px;
}

.shortcuts-box kbd {
  background: var(--surface-3);
  border: 1px solid var(--border-medium);
  padding: 2px 8px;
  border-radius: 5px;
  font-size: 12px;
  color: var(--text-primary);
  font-family: 'SF Mono', 'Fira Code', monospace;
  box-shadow: 0 1px 2px rgba(0,0,0,0.2);
}
.shortcuts-box kbd.accent { color: #60a5fa; }
```

- [ ] **Step 2: Replace shortcuts overlay HTML**

Replace the entire `shortcuts-overlay` div:

```html
<div class="shortcuts-overlay" id="shortcuts">
  <div class="shortcuts-box">
    <h3>Keyboard Shortcuts</h3>

    <div class="shortcuts-section">
      <div class="shortcuts-section-title">Navigation</div>
      <div class="shortcuts-grid">
        <div><kbd>Esc</kbd> Return to command bar</div>
        <div><kbd class="accent">Ctrl</kbd>+<kbd>1</kbd>-<kbd>4</kbd> Focus pane</div>
      </div>
    </div>

    <div class="shortcuts-section">
      <div class="shortcuts-section-title">Actions</div>
      <div class="shortcuts-grid">
        <div><kbd class="accent">Ctrl</kbd>+<kbd>F</kbd> Search in pane</div>
        <div><kbd class="accent">Ctrl</kbd>+<kbd>L</kbd> Clear pane</div>
        <div><kbd class="accent">Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>L</kbd> Toggle layout</div>
        <div><kbd>?</kbd> This help</div>
      </div>
    </div>

    <div class="shortcuts-section">
      <div class="shortcuts-section-title">Command Bar</div>
      <div class="shortcuts-grid">
        <div><kbd>1</kbd>-<kbd>4</kbd> prefix = target pane</div>
        <div><kbd>*</kbd> prefix = all panes</div>
        <div>Single char = raw keypress</div>
        <div>Multi-char = command + Enter</div>
      </div>
    </div>

    <div style="margin-top: 8px; color: var(--text-muted); font-size: 12px;">
      Click pane title to rename &middot; Click backdrop to close
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add backdrop click to close**

In the JS, find the shortcuts overlay close behavior. Add after the existing keyboard shortcut handler for `?`:

```js
document.getElementById('shortcuts').addEventListener('click', (e) => {
  if (e.target === e.currentTarget) {
    e.currentTarget.classList.remove('open');
  }
});
```

- [ ] **Step 4: Verify shortcuts overlay**

Open in browser. Type `?` in input bar. Confirm:
- Frosted backdrop with blur
- Glass modal with scale animation
- Two-column grid layout with section headers
- Glass kbd pills with accent coloring
- Click backdrop or Esc to close

- [ ] **Step 5: Commit**

```bash
cd "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux"
git add quadmux.html
git commit -m "feat: glass keyboard shortcuts modal with grouped two-column layout"
```

---

### Task 6: Layout Transition Animations

**Files:**
- Modify: `quadmux.html` (CSS transitions on grid, JS timing)

- [ ] **Step 1: Add grid transition CSS**

Add to the `.grid` style:

```css
transition: grid-template-columns var(--transition-slow), grid-template-rows var(--transition-slow);
```

- [ ] **Step 2: Add shell transition for grid repositioning**

Add to the `.shell` style:

```css
transition: border-color var(--transition-normal), box-shadow var(--transition-normal), opacity var(--transition-slow), grid-column var(--transition-slow), grid-row var(--transition-slow);
```

Note: `grid-column` and `grid-row` are not animatable in CSS. The grid-template transition will handle the smooth resize. The panes will reflow naturally.

- [ ] **Step 3: Update fitAll delay in layout switch**

In the `setLayout` function, find:

```js
setTimeout(fitAll, 50);
```

Replace with:

```js
setTimeout(fitAll, 350);
```

This waits for the `300ms` CSS transition to complete before re-fitting terminals.

- [ ] **Step 4: Verify layout transitions**

Open in browser. Click the layout toggle button. Confirm:
- Smooth 300ms transition between 2x2, 1x4, and 4x1 layouts
- No terminal rendering glitch after transition
- Terminals re-fit correctly after animation

- [ ] **Step 5: Commit**

```bash
cd "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux"
git add quadmux.html
git commit -m "feat: smooth CSS transitions for layout switching"
```

---

### Task 7: Terminal Theme Update & Final Polish

**Files:**
- Modify: `quadmux.html` (xterm theme in JS, final CSS touches)

- [ ] **Step 1: Update xterm terminal theme**

In the JS where terminals are created, find:

```js
theme: { background: '#111111', foreground: '#e0e0e0', cursor: '#e0e0e0' },
```

Replace with:

```js
theme: {
  background: 'rgba(0,0,0,0)',
  foreground: '#e2e8f0',
  cursor: '#e2e8f0',
  cursorAccent: '#0a0e17',
  selectionBackground: 'rgba(255,255,255,0.15)',
  selectionForeground: '#ffffff',
},
```

The transparent background lets the pane's glass surface show through.

- [ ] **Step 2: Add input bar focus pulse on Esc return**

In the `setActiveShell` function, in the `else` branch (returning to bar), add after `inputEl.focus();`:

```js
inputEl.style.boxShadow = '0 0 0 2px rgba(148,163,184,0.3)';
setTimeout(() => { inputEl.style.boxShadow = ''; }, 300);
```

Wait -- the input no longer has a visible border/shadow by default. Instead, pulse the input bar container. Update the else branch to:

```js
} else {
    focusLocked = false;
    inputEl.focus();
    modeHint.textContent = 'command mode';
    modeHint.style.color = 'var(--text-muted)';
    modeHint.classList.add('visible');
    clearTimeout(modeHint._fadeTimer);
    modeHint._fadeTimer = setTimeout(() => modeHint.classList.remove('visible'), 2000);
    // Brief pulse on input bar
    const bar = document.querySelector('.input-bar');
    bar.style.borderColor = 'rgba(148,163,184,0.3)';
    setTimeout(() => { bar.style.borderColor = ''; }, 300);
  }
```

- [ ] **Step 3: Add command sent flash to target pane header**

In the input submit handler (`inputEl.addEventListener('keydown', ...)`), after the `ws.send` call for a targeted pane, add a header flash. Find:

```js
} else {
      const idx = parseInt(val[0]) - 1;
      ws.send(JSON.stringify({ type: 'input', shell: idx, text: cmd, raw }));
      if (!raw) timerStart[idx] = Date.now();
      terms[idx].scrollToBottom();
    }
```

Add after `terms[idx].scrollToBottom();`:

```js
      shellEls[idx].classList.remove('flash');
      void shellEls[idx].offsetWidth;
      shellEls[idx].classList.add('flash');
```

And for the `isAll` branch, after the for loop:

```js
    for (let i = 0; i < 4; i++) {
      shellEls[i].classList.remove('flash');
      void shellEls[i].offsetWidth;
      shellEls[i].classList.add('flash');
    }
```

- [ ] **Step 4: Update xterm font family for consistency**

In the terminal creation, find:

```js
fontFamily: "'SF Mono', 'Fira Code', 'Consolas', monospace",
```

This is fine as-is. No change needed.

- [ ] **Step 5: Full integration test**

Run the actual QuadMux server and open in browser:

```bash
cd "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux"
python3 quadmux-server.py &
sleep 2
open http://localhost:8765
```

Verify:
- Navy gradient background
- Frosted glass pane headers with health lines
- Floating command palette with pill targets
- Smooth layout transitions
- Glass shortcuts modal
- Auto-approve toasts (when triggered)
- All keyboard shortcuts work
- Terminals are readable with transparent background

Kill the server after testing:

```bash
kill %1
```

- [ ] **Step 6: Commit**

```bash
cd "/Users/seanreel2022/Desktop/Golf - AI & Automation (AI)/quadmux"
git add quadmux.html
git commit -m "feat: terminal theme, focus pulse, command flash -- glass cockpit complete"
```

---

## Summary

| Task | Description | Key Changes |
|------|-------------|-------------|
| 1 | Visual Foundation | Gradient bg, CSS variables, glass surfaces, scrollbars |
| 2 | Pane Headers | Glass headers, health line, focus dot, icon mic |
| 3 | Command Palette | Floating bar, inline pills, icon buttons, status dot |
| 4 | Auto-Approve Visibility | Toast notifications, REVIEW badge |
| 5 | Shortcuts Overlay | Glass modal, two-column grid, backdrop close |
| 6 | Layout Transitions | Smooth CSS grid transitions |
| 7 | Final Polish | Terminal theme, focus pulse, command flash |
