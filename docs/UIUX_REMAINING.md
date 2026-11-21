# UI/UX work remaining on `fix/uiux-harden-audit`

Status as of 2026-11-20, WIP commit 402fb66. Sources: the Impeccable audit
(`.impeccable/audit/2026-11-20T15-40-54Z__project.md`) and the design
critique run in the same session.

## 1. Finish and verify the Studio-style Design → Send hand-off (blocking)

The user said "Review in Send" doesn't match Silhouette Studio. Their decision:

- Switching to **Send** sends the edited design, as Studio's SEND tab does.
  Cut versus Score is inferred: one action gives a standard send, mixed
  actions give per-action groups.
- An untouched or empty design keeps Send's artwork.
- The footer button appears only for sticker sheets ("Export Print & Cut…").

The code is in place (`editor_model.send_handoff`, `send_action`;
`send_from_design` and `forget_sent_design` in `qt_gui.py` and `gui.py`) but
is **not verified**:

- [x] `python3 -m unittest tests.test_qt_frontend` hangs. Run it verbosely
      with `python3 -u`, writing to a file in the background, to find the test.
      Suspects: the sticker test (around lines 148–165) now reaches
      `send_from_design` with the "stickers" verdict, or one of the new
      hand-off tests.
- [x] Update the GTK tests that still use the removed button.
      `design.review.emit('clicked')` (around lines 343, 415, 547, 655, 709,
      722) becomes `design.send_workspace.emit('clicked')`. The
      `design.review` sensitivity check around line 90 now needs to test
      visibility.
- [x] Add GTK versions of the four new Qt hand-off tests
      (`test_switching_to_send_sends_the_edited_design_like_studio` and the
      three after it).
- [x] Mutation-check `send_handoff`: break the `applied` / `origin` checks
      and confirm `TestSendHandoff` fails.
- [x] Run the full `./run-tests.sh` without piping it, then
      `cut-proxies cut --dry-run`.

The rename of "Open Cut Artwork…" to "Open Cut Template…" is done, and both
toolkits now title that group "Cut template". It is covered by the same test
run.

## 2. Critique priority issues still open

- [x] **Send confirmation (P1, harden).** Qt still uses a Yes/No
      `QMessageBox` (`qt_gui.py` `confirm`). Build one shared model for both
      toolkits:
  - Title "Send this cut?", lead sentence "This moves the blade."
  - Warnings first, then settings, then the connection.
  - Buttons "Cut" (destructive) and "Cancel", with Cancel as the default.
  - Also fix GTK's scan-only path, which replaces the whole body
    (`gui.py`, around `body = "This test ends…"`).
- [x] **Show the verification (P1, clarify).** The summary card and the first
      line of the confirmation should state what was checked ("8 paths decoded
      and measured inside the sheet"). Keep the raw table under Advanced.
- [x] **First run (P2, onboard).** The user chose "open the last-used state".
      A brand-new user gets an empty page with "Open a cut template" and
      "Draw in Design" as next steps. Record the rule in `PRODUCT.md`.
- [x] **Design workspace (P2, layout).**
  - Replace the Tool dropdown with a vertical strip of tool buttons (current
    one checked) plus single-key tool shortcuts.
  - Name objects by kind and size ("Rounded rect 63×88"), not "1. Path".
  - Collapse a side panel below about 900 px so the canvas keeps at least
    half the width.

## 3. Audit items still open

- [x] The footer shows "⚠ Connect a cutter." as a warning on every page
      (`cutters.py:53`). Use neutral wording until the user reaches Review.
- [x] The crowded-card tint relies on colour alone, and Colorblind mode
      doesn't change it (`qt_canvas.py:312`, `gui.py:446`). Name the cards in
      the summary and add a hatch pattern or a label.
- [x] Remaining "(s)" plurals: `editor.py` (around line 1322), `gui.py`
      (around lines 1669, 2018, 2548). The status lines are already fixed.
- [x] Qt redraws the whole scene on every drag move (`qt_design.py`
      `update_drag`). Measure with a heavy import before optimising.
- [x] Give disabled buttons a reason, e.g. Measure Registration Marks:
      "Open a PDF first".

## 4. Before the PR

- [x] `/impeccable polish` across both toolkits (audit and critique resolved).
- [ ] Squash the WIP commit, write the PR description, and run
      `cut-proxies status`.
- [x] `tools/audit-gui.py` is stale (it fails partway through its checks).
      Fix it or retire it.
