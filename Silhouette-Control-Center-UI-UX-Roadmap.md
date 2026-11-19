# Silhouette Control Center UI and UX repair roadmap

Scope covers the Qt and GTK desktop frontends. The roadmap follows the audit of the PR #16 stack. The standalone PDF issue was also confirmed in the send guards on main. Before implementing, check which stacked changes have landed and build each patch on the branch that owns the affected behavior.

## Phase 1 · Make Cut Job reflect what the cutter will do

### 1. Block a failed standalone PDF check when registration is on

**Change.** Use a shared registration-check decision in Qt and GTK. Keep the current check bound to the loaded file, page, and effective registration geometry. Track checking, passed, failed, and could not measure separately. For a registration-enabled job with a print file open, allow sending only when the current check has passed. Disable Send to Cutter, Corner Test Cut, and Test Registration while a check is pending or blocked. Recheck the same decision when the user confirms an action, before opening the cutter connection. Show the reason beside the footer action and link directly to the registration results in Sheet. Use copy such as **Registration check failed. Open Sheet to see the missing marks.** Keep the layout result and PDF result separate so a valid cut layout cannot hide a failed print check.

**Implementation notes.** Wire the decision into Qt’s `send_refusal` and GTK’s `update_send_actions`, `on_cut`, and `_cut_response`. Invalidate the check when the file, page, or effective registration geometry changes. Retain a successful check when only force, speed, or pass count changes. Discard late results from a superseded check. Keep the current device-scan workflow for jobs with no PDF open and the existing blank-stock workflow when registration is off.

**Done when.** In both frontends, a blank PDF, a wrong page, and a measurement error surface the reason and prevent hardware actions. A matching PDF enables eligible actions. A stale result or previously opened confirmation cannot authorize a changed job. Intentional no-PDF and registration-off workflows continue to work.

### 2. Keep active and paused job details tied to the submitted settings

**Change.** On send, keep the confirmed job, cutter, tool holder, registration state, and visible summary together. Display that job’s values and preview while the cutter is active. Make the relevant job controls read-only and keep job actions in a persistent footer. Use **Pausing…** after a pause request and **Resume Cut** when the sender has reached a paused state. Keep cancellation available while waiting for that pause. Use **Stopping…** while cancellation is pending.

**Implementation notes.** Reuse the existing job plan and cut-control state. Avoid rebuilding the active preview from editable settings while the sender still owns the job. Drive button availability and status copy from actual control state, including finishing and errors. Restore editable controls on completion, cancellation, or connection failure.

**Done when.** A simulated job sent at Force 25 keeps showing 25 through pause and resume. Layout, registration, cutter selection, and cut settings cannot change the displayed active job. The UI distinguishes running, pause requested, paused, cancelling, and finishing. Every terminal path returns the UI to an editable state.

## Phase 2 · Keep material testing and navigation predictable

### 3. Keep material testing open while settings are adjusted

**Change.** Give Dial In a Material its own view within Cut Job. Keep the numbered grid, selected spot, pass count, and **Cut One Pass** action in view while the user edits test settings. Reuse the existing cut-settings controls. Add a visible **Exit Material Test** action. Present **Resume Material Test** wherever an unfinished test can be reopened. Keep full-sheet send actions out of the test view.

**Implementation notes.** Opening settings or selecting another tab must leave the spot and its history intact. If the user chooses to test changed settings after cutting a spot, explain why a fresh spot is needed and let them choose one. Do not advance merely because a settings panel opened. Preserve the existing distinction between blank and registered test stock and restore marked-sheet history through its current persistence path.

**Done when.** Starting a test, performing repeated passes, opening settings, switching tabs, and returning all preserve the selected spot and recorded count. A settings change does not silently advance to another spot. Selecting a fresh spot starts its own count. The full-sheet send action returns only after the user explicitly exits the test.

### 4. Make every test spot selectable by keyboard

**Change.** Add a labeled **Test Spot** number selector above the grid. Keep it synchronized with canvas clicks and **Next Test Spot**. Display the selected spot’s completed passes and interrupted status beside it. Give the selected cell a visible outline that remains distinguishable without color, and bring it into view when the canvas is zoomed.

**Implementation notes.** Use the same selection method for the selector and canvas. Limit it to valid spots on the current sheet and retain the existing guarded behavior for interrupted or archived spots. Keep **Next Test Spot** as a shortcut to untouched stock.

**Done when.** With the keyboard alone, choose spot 1, jump to a later spot, and return to a tested spot. The grid highlight, selector and pass count agree. Repeat for blank and registered sheets, with Tab focus visible throughout.

### 5. Restore workspace position and explain matched-ZIP restrictions

**Change.** Restore the last Send tab when the user toggles from Design back to Send. Keep the existing **Review in Send** command as the explicit route to review the edited job. Replace the matched-ZIP dead end with a persistent explanation and a direct **Open Sheet** action. Keep clearing the matched job an explicit action on Sheet.

**Implementation notes.** Store workspace navigation state separately from the review destination. Prefer preserving any already-open Design document independently of the matched job when the current data model allows it. If the matched paths are locked, say so before the user attempts an edit. Avoid a generic “finish the current job” message when the cutter is idle.

**Done when.** Materials → Design → Send restores Materials. **Review in Send** still takes the user to the appropriate review page. Opening a matched ZIP leaves a visible route to Sheet and never clears the matched job through a workspace toggle.

### 6. Give presets and individual cut settings distinct homes

**Change.** Rename **Materials** to **Material Presets** and use it for saving, updating, and importing presets. Keep material selection and individual settings such as Force and Speed in **Cut Job → Cut Settings**. Add **Edit Cut Settings** in Material Presets and anchor it at that heading. Put **Dial In a Material** beside the Cut Settings heading in both versions, with space to wrap at compact widths.

**Done when.** At 850×600, opening Cut Job shows the entry point for material testing without scrolling through blade settings. From Material Presets, one action lands on the editable controls. Editing a job and saving a preset have distinct labels and predictable effects.

## Phase 3 · Fix compact layout, legibility, and styling

### 7. Make the Qt Design inspector fit small windows

**Change.** At wide widths, keep the existing inspector tabs when all labels fit. At narrow widths, use a full-width section selector similar to GTK’s inspector. Let form rows stack as needed, wrap long labels, and keep the inspector vertically scrollable. Size the canvas and inspector so both remain usable at the minimum window size.

**Implementation notes.** Use the available inspector width and control size hints to choose the presentation. Keep one set of inspector pages so the current section and settings survive a resize. Start in `cut_proxies/qt_design.py` and put reusable control styling in `cut_proxies/qt_controls.py`.

**Done when.** At 720×520 and 850×600, all inspector sections are reachable, the full registration checkbox label is readable, and there is no horizontal scrollbar or overlapping tab arrow. At 1200×760, the inspector has room for its wide layout. Repeat with larger text.

### 8. Standardize footer alignment, action styling, and warnings

**Change.** Use consistent button heights, internal padding, and icon alignment within each footer. Make **Send to Cutter** the standard primary action in both versions. Use secondary styling for **Test Registration** and **Corner Test Cut** and destructive styling for **Cancel Cut**. At compact widths, wrap the status or action row while preserving action order. Use readable warning text and an icon or explicit label for registration problems. Keep menu hover and keyboard focus visible without covering or displacing labels.

**Implementation notes.** Align by shared button roles in the Qt and GTK control helpers. Use each toolkit’s theme colors and check normal, selected, focused, hovered, pressed, and disabled states. Preserve the existing mouse-wheel guard on settings and the orientation arrow in both workspaces.

**Done when.** At compact and wide sizes, footer labels and icons are centered, essential actions remain visible, and registration warning text is readable in light and dark themes. A keyboard-only pass shows focus at every step. Scrolling over a focused numeric setting moves the panel without changing the value.

## Delivery and verification

Land the registration guard on main independently because it affects the released workflow. Follow with active-job state, material-test and workspace navigation, then responsive layout and styling. Keep each behavior change in one reviewable patch across Qt and GTK where it affects both.

Run automated checks for registration gating, stale measurement results, active and paused jobs, and preserved test-spot history. Walk both frontends at compact and wide sizes in light and dark themes. Capture the worst-case compact layouts and verify keyboard navigation. Before release, use a connected Cameo 4 to verify a matching Print & Cut sheet, a blocked mismatched sheet, and pause, resume, and cancel. The audit used a simulated connection, so hardware verification is still pending.

## References

[PR #16 audit baseline](https://github.com/zoro11031/silhouette-cameo-linux/pull/16) · [Qt send guard on main](https://github.com/zoro11031/silhouette-cameo-linux/blob/3bb8e75d2018064a6419714d47ea936b75bf3273/cut_proxies/qt_gui.py#L951) · [GTK send guard on main](https://github.com/zoro11031/silhouette-cameo-linux/blob/3bb8e75d2018064a6419714d47ea936b75bf3273/cut_proxies/gui.py#L2897)
