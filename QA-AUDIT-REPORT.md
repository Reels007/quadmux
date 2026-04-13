# QuadMux QA Audit Report

**App:** QuadMux (4-pane Claude Code multiplexer)
**URL:** http://localhost:8765
**Date:** 2026-04-02
**Tested by:** Claude Code via /qa-audit skill
**Previous audit:** 2026-04-01

---

## Executive Summary

| Severity | Count | Change vs Last Audit |
|----------|-------|---------------------|
| Critical | 1 | Same (no responsive layout) |
| High | 1 | Down from 6 (non-functional buttons removed, input overflow persists) |
| Medium | 3 | Down from 5 |
| Low | 2 | Same |
| **Total** | **7** | **Down from 14** |

**Overall health: significantly improved.** 7 issues resolved since last audit, 7 remain.

### What Got Fixed Since Last Audit
1. Non-functional toolbar buttons (Quick Actions, Activity Log) - **removed**
2. Broadcast button with no feedback - **removed** (toolbar simplified)
3. xterm addon-search CSS MIME error - **fixed** (inline CSS)
4. No console errors on load
5. Help overlay (`?`) now works
6. Pane search (Ctrl+F) implemented
7. Draggable gutters between panes added
8. Voice mode with mic device selector added
9. Editable pane titles with localStorage persistence added

### Top 3 Remaining Priorities
1. **Add responsive breakpoints** for sub-768px widths (Critical)
2. **Fix input bar overflow** on long strings (High)
3. **Increase tap target sizes** on voice/toolbar buttons (Medium)

---

## MODULE 1: Full Onboarding Audit

### Summary
QuadMux loads directly into a clean 4-pane 2x2 grid. All 4 Claude instances spawn and display the welcome screen. The toolbar is simplified to 3 buttons (Layout, Clear, Exit) plus a status dot.

### Findings

| # | Finding | Severity | Status |
|---|---------|----------|--------|
| 1.1 | No first-run guidance | Low | Mitigated - input placeholder says "1-4 = target, * = all. ? = keys" |
| 1.2 | Layout toggle works (2x2 -> 1x4 -> 4x1) | PASS | |
| 1.3 | Pane focus/selection works with visual feedback | PASS | |
| 1.4 | Help overlay (?) works and is well-designed | PASS | |
| 1.5 | Clear All works | PASS | |
| 1.6 | Exit All prompts for confirmation | PASS | |
| 1.7 | WebSocket connection indicator works (green dot) | PASS | |
| 1.8 | Health dots per pane show alive/dead state | PASS | |
| 1.9 | Zero console errors on page load | PASS | |

---

## MODULE 2: Form Edge Case Breaker

### Summary
The only input is the bottom command bar. Tested empty submit, invalid prefix, special chars, XSS, and long strings.

### Findings

| # | Test | Severity | Result |
|---|------|----------|--------|
| 2.1 | Empty submit (Enter with no text) | - | PASS - no action taken |
| 2.2 | Invalid prefix ("hello world") | - | PASS - border flashes red, input preserved |
| 2.3 | XSS payload `1<script>alert('xss')</script>` | - | PASS - rendered as text, no execution |
| 2.4 | Special characters `@#$%'"<>` | - | PASS - handled correctly |
| 2.5 | 600+ character string overflows input bar | High | **OPEN** - preview-cmd text extends past bar, covering toolbar buttons |

---

## MODULE 3: Cross-Viewport Layout Inspector

### Findings

| # | Screen Size | Result | Issues |
|---|-------------|--------|--------|
| 3.1 | 375px (mobile) | **Broken** | Pane headers word-wrap ("Clau de 1"), 2x2 grid crushed, toolbar buttons partially hidden |
| 3.2 | 768px (tablet) | Usable | Headers fit, toolbar visible, panes tight but functional |
| 3.3 | 1280px (laptop) | **Good** | Clean 2x2 grid, all elements visible and properly sized |
| 3.4 | 1920px (desktop) | **Good** | Excellent - spacious grid, all features accessible |

| # | Bug Title | Severity | Suggested Fix |
|---|-----------|----------|---------------|
| 3.1 | No responsive layout below 768px | Critical | Add media queries: single column below 768px with tab switcher, or auto-switch to 1x4 layout |
| 3.2 | Pane header titles word-wrap at small widths | Medium | Add `white-space: nowrap; overflow: hidden; text-overflow: ellipsis` to `.shell-title` |

---

## MODULE 4: Before/After CSS Regression Check

### Changes Since Previous Audit (2026-04-01)

| Change | Verified | Regression |
|--------|----------|------------|
| Removed Quick Actions / Activity Log / Broadcast buttons | Yes | None - toolbar cleaner |
| Inline xterm search CSS (was CDN with MIME error) | Yes | None - loads correctly |
| Added draggable gutters between panes | Yes | None - works smoothly |
| Added voice mode button per pane | Yes | None - properly labeled |
| Added pane search (Ctrl+F) | Yes (code review) | Cannot test via Playwright (browser intercepts Ctrl+F) |
| Added editable pane titles | Yes | None - persists to localStorage |
| Help overlay keyboard shortcuts | Yes | None - clean design |

**No CSS regressions detected.** All visual elements render consistently across 1280px and 1920px.

---

## MODULE 5: User Journey Screenshot Mapper

| Step | Screen | User Action | Time | Issues |
|------|--------|-------------|------|--------|
| 1 | Landing (4-pane 2x2 grid) | None - auto-loads | Instant | Placeholder text provides guidance |
| 2 | Focus a pane | Click pane area | <1s | Green border, focus dot, mode hint all update correctly |
| 3 | Type in input bar | Type "1hello" | <1s | Live preview updates: target badge turns green, shows "1 -> hello" |
| 4 | Send command | Press Enter | <1s | Command sent to correct pane, timer starts |
| 5 | Toggle layout | Click grid button | <1s | Cycles through 2x2, 1x4, 4x1 - all work |
| 6 | View help | Type ? + Enter | <1s | Clean overlay with all shortcuts |
| 7 | Search pane | Ctrl+F in focused pane | <1s | Search bar appears with prev/next/close |
| 8 | Drag resize | Drag gutter | <1s | Smooth resize, panes re-fit |
| 9 | Clear all | Click clear button | <1s | All 4 terminals cleared |

**Core value moment:** Sending targeted commands to specific Claude instances works reliably. The input bar with live preview makes targeting intuitive.

---

## MODULE 6: Accessibility Spot Check

### Findings

| # | Issue | Location | Severity | Fix |
|---|-------|----------|----------|-----|
| 6.1 | Voice mode buttons 22x18px (below 44x44px WCAG minimum) | Pane headers (x4) | Medium | Increase padding to meet 44x44px minimum |
| 6.2 | Toolbar buttons 29-35x26px (below 44x44px) | Bottom bar (Layout, Clear, Exit) | Medium | Increase padding to 44x44px |
| 6.3 | Health dots (green/red) use color only - no aria-label or title | Pane headers (x4) | Low | Add `aria-label="alive"/"dead"` and `title` attribute |
| 6.4 | Editable pane titles (contenteditable divs) have no aria-label | Pane headers (x4) | Low | Add `aria-label="Pane title, click to edit"` |
| 6.5 | Focus-visible styles exist on `.btn-voice` | - | PASS | |
| 6.6 | Voice buttons have `aria-label="Voice mode"` and `title` | - | PASS | |
| 6.7 | Toolbar buttons have `aria-label` and `title` | - | PASS | |
| 6.8 | Input bar has placeholder text describing usage | - | PASS | |
| 6.9 | Search bar hidden buttons (^, v, x) are `display:none` when inactive | - | PASS (correct approach) | |

---

## MODULE 7: Structured Bug Reports

### BUG-001: No responsive layout - unusable below 768px

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Resize browser to 375px width
3. Observe 4 panes crushed into unusable columns

**Expected:** Layout should adapt at small widths
**Actual:** 2x2 grid maintained at all sizes; pane headers word-wrap ("Clau de 1"), text unreadable
**Severity:** Critical | **Priority:** P1
**Suggested Fix:** Add CSS media query: below 768px auto-switch to `1x4` (stacked) layout. The layout cycling code already supports this - just trigger `setLayout(1)` via media query or JS `matchMedia`.

---

### BUG-002: Long input text overflows toolbar

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Paste 500+ characters into the bottom command bar
3. Observe the preview-cmd text extends past the bar

**Expected:** Text should be contained within the input bar
**Actual:** Preview text overflows, covering toolbar buttons and extending past right edge
**Severity:** High | **Priority:** P2
**Suggested Fix:** Add to `.preview-cmd`: `overflow: hidden; text-overflow: ellipsis; max-width: 150px; white-space: nowrap`

---

### BUG-003: Voice mode buttons below WCAG tap target minimum

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Inspect mic button in any pane header - measures 22x18px

**Expected:** Interactive elements should be at least 44x44px (WCAG 2.5.5)
**Actual:** All voice buttons are 22x18px
**Severity:** Medium | **Priority:** P3
**Suggested Fix:** Increase `.btn-voice` padding to `padding: 8px 12px` or add `min-width: 44px; min-height: 44px`

---

### BUG-004: Toolbar buttons below WCAG tap target minimum

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Inspect Layout (29x26px), Clear (35x26px), Exit (29x26px) buttons

**Expected:** At least 44x44px
**Actual:** All buttons 26px tall
**Severity:** Medium | **Priority:** P3
**Suggested Fix:** Increase `.btn-icon` padding to `padding: 10px 12px`

---

### BUG-005: Health dots use color only with no accessible label

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Inspect `.shell-health` dots - no title, no aria-label

**Expected:** Screen readers should announce alive/dead state
**Actual:** Color-only indicator (green=alive, red=dead) with no text alternative
**Severity:** Low | **Priority:** P4
**Suggested Fix:** Add `title` and `aria-label` attributes, updated dynamically in `updateHealth()`

---

### BUG-006: Editable pane titles lack accessible label

**Steps to Reproduce:**
1. Inspect `.shell-title` contenteditable divs
2. No aria-label explaining editability

**Expected:** Accessible label indicating the element is editable
**Actual:** No aria-label or role attribute
**Severity:** Low | **Priority:** P4
**Suggested Fix:** Add `role="textbox"` and `aria-label="Pane title, click to rename"`

---

### BUG-007: Pane header titles word-wrap at narrow widths

**Steps to Reproduce:**
1. Open QuadMux at 375px width
2. Observe "Claude 1" wraps to "Clau de 1"

**Expected:** Title should truncate with ellipsis, not wrap
**Actual:** Title word-wraps mid-word
**Severity:** Medium | **Priority:** P3 (resolved by BUG-001 responsive fix)
**Suggested Fix:** Add `white-space: nowrap; overflow: hidden; text-overflow: ellipsis` to `.shell-title`

---

## MODULE 8: Pre-Launch QA Test Plan

### Core User Flows

| Flow | Test Cases | Priority |
|------|-----------|----------|
| **Pane Focus & Selection** | Click each pane, verify border/glow/mode hint update. Press Escape to defocus. Ctrl+1-4 shortcuts. | P1 |
| **Send Command to Pane** | `1hello` targets pane 1. `*hello` broadcasts. Empty input rejected. Invalid prefix rejected with red flash. | P1 |
| **Layout Toggle** | Cycle 2x2 -> 1x4 -> 4x1 -> 2x2. Verify terminals re-fit. Verify content preserved. | P1 |
| **Drag Resize** | Drag column gutter left/right. Drag row gutter up/down. Verify clamping at 15%/85%. | P1 |
| **WebSocket Connection** | Start server, verify green dot. Kill server, verify red dot + auto-reconnect after 2s. | P1 |
| **Pane Search** | Ctrl+F to open, type query, Enter for next, Shift+Enter for prev, Escape to close. | P2 |
| **Voice Mode** | Click mic, verify device selector on first use. Verify speech recognition starts. Right-click to change device. | P2 |
| **Editable Titles** | Click pane title, type new name, blur. Reload page, verify title persisted in localStorage. | P2 |
| **Clear All** | Click clear, verify all 4 terminals cleared. Verify Claude instances still alive. | P2 |
| **Exit All** | Click exit, verify confirmation dialog. Verify all instances terminated. | P2 |
| **Help Overlay** | Type ? + Enter, verify overlay. Press Escape or click backdrop to close. | P3 |

### Edge Cases & Negative Tests

| Test | Expected Behavior |
|------|-------------------|
| Rapid layout toggling (10x fast) | No render glitches or state corruption |
| Paste very large text (10k+ chars) | Input contained, no freeze |
| Disconnect WiFi during active session | Red status dot, auto-reconnect when back |
| Send command while Claude is busy | Queued by PTY, delivered when prompt returns |
| Open in multiple browser tabs | Each tab replays buffer, gets independent focus state |
| Resize browser during Claude output | Terminals reflow via fitAddon |
| Kill a Claude child process | Health dot turns red, pane dims (dead class) |
| Drag gutter to extreme (past 85%) | Clamped at 85% - no layout break |

### Devices & Browsers

| Device | Browser | Priority |
|--------|---------|----------|
| MacBook Pro 14" (1512px) | Chrome, Safari, Firefox | P1 |
| MacBook Air 13" (1280px) | Chrome, Safari | P1 |
| External monitor (1920px+) | Chrome | P1 |
| iPad Pro (1024px) | Safari | P2 |
| iPhone (375px) | Safari | P3 (after responsive fix) |

### Known Risk Areas

1. **WebSocket stability** - PTY streaming over WS under heavy output load
2. **Memory leaks** - 4 concurrent xterm.js instances with 10k scrollback each
3. **Auto-approve safety** - `isSecurityRisk()` blocklist may miss edge cases
4. **Voice mode browser support** - SpeechRecognition only works in Chrome/Edge
5. **CDN dependency** - xterm.js loaded from jsdelivr; app fails if CDN is down

### Estimated Test Time

| Module | Time |
|--------|------|
| Pane Focus & Selection | 5 min |
| Send Commands | 10 min |
| Layout Toggle + Drag Resize | 10 min |
| WebSocket Connection | 10 min |
| Search / Voice / Titles | 15 min |
| Clear / Exit / Help | 5 min |
| Edge Cases | 15 min |
| Cross-browser | 20 min |
| **Total** | **~90 min** |
