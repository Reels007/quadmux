# QuadMux QA Audit Report

**App:** QuadMux (4-pane Claude Code multiplexer)
**URL:** http://localhost:8765
**Date:** 2026-04-01
**Tested by:** Claude Code via /qa-audit skill

---

## MODULE 1: Full Onboarding Audit

### Summary
QuadMux launches directly into a 4-pane terminal grid with no onboarding flow. This is appropriate for a power-user tool, but there is no help text, tooltips, or first-run guidance.

### Findings

| # | Bug Title | Severity | Suggested Fix |
|---|-----------|----------|---------------|
| 1.1 | No first-run help or tooltip explaining controls | Medium | Add a dismissible overlay or `?` button showing keyboard shortcuts and button functions |
| 1.2 | Broadcast button (toolbar) has no visible feedback when clicked | Medium | Show a toast/highlight or toggle state indicating broadcast mode is on/off |
| 1.3 | Quick Actions button has no visible effect when clicked | High | Either implement the dropdown menu or remove the button |
| 1.4 | Activity Log button has no visible effect when clicked | High | Either implement the activity log panel or remove the button |
| 1.5 | Maximize button does not maximize pane to full screen | High | Fix maximize to expand selected pane to fill the grid area, hiding other panes |
| 1.6 | Console error on load: xterm addon-search CSS rejected (MIME type text/plain) | Medium | Self-host the CSS or fix the CDN URL to serve correct MIME type |

---

## MODULE 2: Form Edge Case Breaker

### Summary
The only user input is the bottom command bar. Tested with special characters, XSS payloads, and long strings.

### Findings

| # | Bug Title | Severity | Suggested Fix |
|---|-----------|----------|---------------|
| 2.1 | XSS payload handled safely - no execution | PASS | N/A |
| 2.2 | Special characters (@#$%'"<>) accepted correctly | PASS | N/A |
| 2.3 | 500+ char string overflows bottom toolbar, covering buttons | High | Add `overflow: hidden; text-overflow: ellipsis` or max-width on input preview area, or allow horizontal scroll |
| 2.4 | No input validation or character limit on command bar | Low | Consider a reasonable max-length to prevent accidental paste floods |
| 2.5 | Empty submit (Enter with no text) - untested due to live Claude sessions | Info | Verify empty submit is handled gracefully |

---

## MODULE 3: Cross-Viewport Layout Inspector

### Summary
QuadMux is designed for desktop. No responsive breakpoints exist. Below 1024px it becomes unusable.

### Findings

| # | Screen Size | Bug Title | Severity |
|---|-------------|-----------|----------|
| 3.1 | 375px (mobile) | All 4 panes crushed into ~90px columns, text unreadable, toolbar buttons hidden | Critical |
| 3.2 | 375px (mobile) | Pane headers truncated ("Clau de 1") | High |
| 3.3 | 375px (mobile) | Bottom toolbar buttons overflow off-screen | High |
| 3.4 | 768px (tablet) | Pane headers still truncated, heavy text wrapping makes content hard to follow | High |
| 3.5 | 768px (tablet) | Status bar text ("bypass permissions on (shift+tab to cycle)") clipped | Medium |
| 3.6 | 1280px (laptop) | Usable but pane status text clipped at bottom | Low |
| 3.7 | 1920px (desktop) | Works well - proper 2x2 grid, full headers, clean toolbar | PASS |

**Recommendation:** Add responsive breakpoints:
- Below 768px: stack panes vertically (1 column) or show single-pane with tab switcher
- 768-1024px: 2x2 grid with collapsed headers
- 1024+: current layout works

---

## MODULE 4: Before/After CSS Regression Check

No recent CSS changes specified. Baseline screenshots captured for future regression testing:
- `quadmux-m1-landing.png` - Default 2x2 grid at 1280px
- `quadmux-m1-layout-toggled.png` - Stacked/rows layout
- `quadmux-m3-1920px.png` - Full HD reference

---

## MODULE 5: User Journey Screenshot Mapper

| Step | Screen | User Action | Time | Issues |
|------|--------|-------------|------|--------|
| 1 | Landing (4-pane grid) | None - auto-loads | Instant | No guidance on what to do |
| 2 | Focus a pane | Click pane header | <1s | Works, "focused" indicator updates in toolbar |
| 3 | Type a command | Type in bottom input bar | <1s | Placeholder text explains targeting ("1-4 = target, * = all") |
| 4 | Toggle layout | Click grid icon | <1s | Works, toggles between 2x2 grid and stacked rows |
| 5 | Maximize a pane | Click maximize icon | <1s | **BROKEN** - does not maximize |
| 6 | Use toolbar buttons | Click Broadcast/Quick Actions/Activity Log | <1s | **BROKEN** - no visible response |

**Core value moment:** Sending a command to a specific Claude pane. This works well. The confusion points are the non-functional toolbar buttons.

---

## MODULE 6: Accessibility Spot Check

**42 total issues found.**

### Critical Accessibility Issues

| # | Issue | Location | Severity | Fix |
|---|-------|----------|----------|-----|
| 6.1 | 5 unlabeled INPUT elements (hidden terminal inputs) | Each pane + command bar | High | Add `aria-label` to all input elements |
| 6.2 | Voice mode buttons too small: 26x18px | Each pane header (x4) | High | Increase to min 44x44px tap target |
| 6.3 | Stop buttons too small: 13x14px | Each pane header (x4) | High | Increase to min 44x44px |
| 6.4 | Maximize buttons too small: 16x14px | Each pane header (x4) | High | Increase to min 44x44px |
| 6.5 | Hidden buttons (^, v, x) have 0x0px size | Each pane header (x4) | Medium | Either display at proper size or use `display:none` + `aria-hidden` |
| 6.6 | Toolbar buttons too small: 28x26px | Bottom toolbar (Broadcast, etc.) | Medium | Increase to min 44x44px |
| 6.7 | No visible focus indicators on buttons | Global | Medium | Add `:focus-visible` outline styles |
| 6.8 | Pane status uses color only (green=idle, yellow=busy) | Pane headers | Medium | Already has text labels ("idle"/"busy") - good. Ensure sufficient contrast. |

---

## MODULE 7: Structured Bug Reports (GitHub Issues)

### BUG-001: Quick Actions and Activity Log buttons are non-functional

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Click the Quick Actions (lightning bolt) button in the bottom toolbar
3. Observe no response
4. Click the Activity Log (hamburger) button
5. Observe no response

**Expected:** Clicking should open a menu/panel with quick actions or activity log
**Actual:** No visible response. No error in console.
**Severity:** High | **Priority:** P2
**Fix:** Implement the dropdown/panel UI, or remove buttons if features are planned for later

---

### BUG-002: Maximize button does not maximize pane

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Click the maximize icon on any pane header
3. Observe the pane does not expand to fill the screen

**Expected:** Pane should expand to fill the entire grid area
**Actual:** Layout changes to stacked rows instead of maximizing the selected pane
**Severity:** High | **Priority:** P2
**Fix:** Implement maximize to hide other panes and expand selected pane to 100%

---

### BUG-003: Long input text overflows toolbar

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Paste 500+ characters into the bottom command bar
3. Observe the text overflows and covers toolbar buttons

**Expected:** Text should be contained within the input area
**Actual:** Text extends beyond input bounds, overlapping toolbar buttons
**Severity:** High | **Priority:** P2
**Fix:** Add `overflow: hidden` and `text-overflow: ellipsis` to the input container, or constrain the preview area

---

### BUG-004: No responsive layout - unusable below 1024px

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Resize browser to 768px or 375px width
3. Observe 4 panes crushed into unusable columns

**Expected:** Layout should adapt - stack panes or show tab switcher at small widths
**Actual:** 4 columns maintained at all sizes, text becomes unreadable
**Severity:** Critical | **Priority:** P1
**Fix:** Add CSS media queries: single column below 768px, 2-column below 1024px

---

### BUG-005: xterm addon-search CSS fails to load (MIME type error)

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Open browser console
3. Observe error: "Refused to apply style from CDN - MIME type text/plain"

**Expected:** CSS should load successfully
**Actual:** CSS rejected due to incorrect MIME type from jsDelivr
**Severity:** Medium | **Priority:** P3
**Fix:** Self-host the CSS file or use a different CDN path that serves correct MIME type

---

### BUG-006: All pane header buttons below WCAG minimum tap target size

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Inspect Voice Mode (26x18px), Stop (13x14px), Maximize (16x14px) buttons

**Expected:** Interactive elements should be at least 44x44px (WCAG 2.5.5)
**Actual:** All buttons significantly undersized
**Severity:** Medium | **Priority:** P3
**Fix:** Increase button padding/size to meet 44x44px minimum, or add larger click targets via padding

---

### BUG-007: Broadcast button provides no visual feedback

**Steps to Reproduce:**
1. Open QuadMux at localhost:8765
2. Click the Broadcast button in the toolbar
3. Observe no visual change or feedback

**Expected:** Toggle state, highlight, or toast notification indicating broadcast mode
**Actual:** No visual feedback
**Severity:** Medium | **Priority:** P2
**Fix:** Add active/toggle styling and a status indicator

---

## MODULE 8: Pre-Launch QA Test Plan

### Core User Flows

| Flow | Test Cases | Priority |
|------|-----------|----------|
| **Pane Focus & Selection** | Click each pane header, verify "focused" indicator updates, verify input targets correct pane | P1 |
| **Send Command to Pane** | Type "1 hello" to target pane 1, type "* hello" to broadcast, test empty input | P1 |
| **Layout Toggle** | Toggle between grid/stacked, verify panes re-render correctly, verify terminal content preserved | P1 |
| **Maximize/Restore** | Maximize each pane, verify full expansion, restore, verify grid returns | P1 |
| **WebSocket Connection** | Start server, verify "connected" indicator, kill server, verify reconnection/error state | P1 |
| **Voice Mode** | Click voice mode on each pane, verify mic access prompt, test start/stop | P2 |
| **Clear All** | Click Clear All, verify all panes reset | P2 |
| **Exit All** | Click Exit All, verify all Claude instances terminated gracefully | P2 |

### Edge Cases & Negative Tests

| Test | Expected Behavior |
|------|-------------------|
| Rapid layout toggling (10x fast) | No render glitches or state corruption |
| Disconnect WiFi during active session | Reconnection attempt, "disconnected" indicator |
| Send command while pane is busy | Command queued or clear feedback that pane is busy |
| Paste very large text (10k+ chars) | Handled gracefully, no freeze |
| Open in multiple browser tabs | Each tab gets independent session |
| Resize browser while Claude is outputting | Terminal reflows without corruption |

### Devices & Browsers

| Device | Browser | Priority |
|--------|---------|----------|
| MacBook Pro 14" (1512px) | Chrome, Safari, Firefox | P1 |
| MacBook Air 13" (1280px) | Chrome, Safari | P1 |
| External monitor (1920px+) | Chrome | P1 |
| iPad Pro (1024px) | Safari | P2 |
| iPhone (375px) | Safari | P3 (after responsive fix) |

### Known Risk Areas

1. **WebSocket stability** - PTY streaming over WS can be fragile under load
2. **Terminal rendering** - xterm.js rendering with multiple panes may have race conditions during rapid resizing
3. **Memory leaks** - 4 concurrent xterm.js instances with continuous output - monitor memory over long sessions
4. **CDN dependency** - xterm.js loaded from CDN; if CDN is down, app is completely broken

### Estimated Test Time

| Module | Time |
|--------|------|
| Pane Focus & Selection | 10 min |
| Send Commands | 15 min |
| Layout Toggle | 10 min |
| Maximize/Restore | 10 min |
| WebSocket Connection | 15 min |
| Voice Mode | 10 min |
| Clear/Exit All | 5 min |
| Edge Cases | 20 min |
| Cross-browser | 30 min |
| **Total** | **~2 hours** |

---

## Executive Summary

| Severity | Count |
|----------|-------|
| Critical | 1 (no responsive layout) |
| High | 6 (non-functional buttons, maximize broken, input overflow, small tap targets) |
| Medium | 5 (no onboarding, CSS error, broadcast feedback, focus indicators) |
| Low | 2 (input validation, status bar clipping) |
| **Total** | **14 unique issues** |

### Top 3 Priorities
1. **Fix non-functional toolbar buttons** (Quick Actions, Activity Log, Broadcast) or remove them
2. **Fix Maximize** to actually expand pane to full screen
3. **Add responsive breakpoints** for sub-1024px widths
