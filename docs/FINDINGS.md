# FINDINGS

What was tried, what happened, what was concluded — including the things that
turned out not to work and the things that are still unverified. The same
record `canon-suite/docs/FINDINGS.md` keeps for the printer.

All work dated **2026-11-07** unless stated otherwise. Sections 11-14 were
added later the same day, after a Cameo 4 was ordered and Silhouette Studio
was installed in the WinBoat guest.

**The headline, before anything else:** no Silhouette cutter has ever been
attached to this machine. A **Cameo 4** is on order; nothing here has been
tried on it. Everything in sections 1–6 is verified by measurement or against
a published reference. Everything in sections 7–9 and 11–12 is built from a
recovered protocol and community documentation and has **never moved a
blade**. Those are different kinds of claim and this file keeps them apart.

---

## 1. The layout algorithm reproduces Silhouette Card Maker exactly

**Method.** Silhouette Card Maker (SCM) publishes `assets/layouts.json`, a
table of computed layouts per paper and card size, each with the grid
dimensions and the optimised registration-mark arm length. Its
`page_manager.py` contains the algorithm that generated that table.
`cut_proxies/geometry.py` reimplements `select_best_margins`,
`compute_grid_fit`, `compute_card_positions` and `generate_layout`, including
their integer-pixel quantisation at 300 ppi.

**Result.** Eight sheet/card combinations were checked against the published
table. All eight match, grid and arm length, to the published two decimal
places:

| Sheet | Card | Orientation | Inset | Grid | Published arm | Ours |
|---|---|---|---|---|---|---|
| Letter | standard | landscape | 10 mm | 4×2 | 8.04 mm | 8.04 |
| Letter | standard | portrait | 3.5 mm | 3×3 | 7.45 mm | 7.45 |
| A4 | standard | landscape | 10 mm | 4×2 | 9.40 mm | 9.40 |
| A4 | standard | portrait | 3.5 mm | 3×3 | 10.50 mm | 10.50 |
| Letter | poker | landscape | 10 mm | 4×2 | 7.20 mm | 7.20 |
| A4 | poker | landscape | 10 mm | 4×2 | 8.38 mm | 8.38 |
| Letter | japanese | landscape | 10 mm | 4×2 | 10.08 mm | 10.08 |
| A4 | japanese | portrait | 10 mm | 3×3 | 6.94 mm | 6.94 |

**Conclusion.** The geometry is trustworthy to the extent SCM's is, which is
the best available standard — SCM's templates are cut daily by a large number
of people. The check lives in
`tests/test_geometry.py::TestPublishedLayouts::test_matches_scm_published_layouts`
and is the single most important test in this project. The integer-pixel
quantisation must be kept: dropping it and working in floats changes the arm
lengths in the third decimal place and the table stops matching.

## 2. A 3×3 nine-card sheet needs the borderless 3.5 mm inset

**What was asked for.** "Standard 3×3 (9-card) US Letter and A4 grid
arrangements."

**What is actually true.** With the standard 10 mm registration inset there is
no 3×3 standard-card layout on Letter in either orientation. The marks take
the room:

```
Letter  10.0 mm  landscape  4x2   8 cards
Letter  10.0 mm  portrait   3x2   6 cards
Letter   3.5 mm  portrait   3x3   9 cards
A4      10.0 mm  landscape  4x2   8 cards
A4       3.5 mm  portrait   3x3   9 cards
```

This matches SCM, which ships the 3×3 layouts only under its `borderless`
registration preset at a 3.5 mm inset, and the 10 mm `default` preset gets
4×2.

**Conclusion.** Nine-up is available and supported — `--mark-inset 3.5
--portrait` — but it is not the default, because a 3.5 mm inset puts the marks
very close to the paper edge and leaves no room for printer feed variation.
Eight-up at 10 mm is the safer sheet. `cut-proxies layouts` prints the whole
table so the trade is visible rather than assumed.

Legal portrait fits 3×3 at the full 10 mm inset with 33 mm of mark clearance
to spare, if nine cards per sheet matters more than paper size.

## 3. Proxy Press and Silhouette Card Maker do not draw the same marks

Proxy Press already prints Silhouette registration marks — `regMarks()` in
`proxy-press.html`, behind its `silhouette` setting. It is not the same
geometry SCM uses, in three ways:

| | SCM | Proxy Press |
|---|---|---|
| Marks | square top-left, L at top-right and bottom-left — **three** | square top-left, L at top-right, bottom-left **and bottom-right** — four |
| Line thickness | 1.0 mm (its default) | 0.5 mm (SCM's permitted minimum) |
| Arm length | optimised per layout, 5–20 mm | fixed 20 mm (SCM's maximum) |

The bottom-right mark is the interesting one. Silhouette's three-mark Type 1
pattern has nothing in that corner, and its four-mark pattern (Cameo 5 Alpha)
has an L at all four corners and **no square**. Proxy Press prints a hybrid of
the two that is neither.

**Not yet established:** whether the extra fourth mark actually confuses a
Cameo's scan. It sits outside the three read zones, so the likeliest outcome
is that it is simply ignored, but that has not been tested and will not be
until there is a cutter here. `cut-proxies` therefore models it explicitly as
a third pattern, `proxypress`, rather than pretending the sheet carries the
canonical three — understating what is on the paper would be the worse error.

**The thickness is worth changing.** 0.5 mm is the thinnest line the machine
is documented to accept, and it is being asked to read it off black-core
cardstock under a laser-printed surface. 1.0 mm is SCM's default for a reason.
If a scan fails on a Proxy Press sheet, raise `regLen`'s neighbour first.

## 4. SCM's specification and its code disagree about the square

`SPECIFICATION.md` says the 5×5 mm square mark is positioned so that "the
center of the square is positioned `inset` mm from both paper edges".

Its code does not do that. `page_manager.generate_reg_mark` emits
`Rectangle((inset_mm, paper_height_mm - inset_mm - 5), 5, 5)` in matplotlib's
lower-left-origin coordinates, which puts the square's **corner** on the
inset, not its centre — a 2.5 mm difference in both axes.

**Conclusion.** The code wins: it is what the published templates were cut
against, and it is independently what Proxy Press draws. `cut_proxies` places
the corner on the inset and says so in `regmarks.py`. If a future SCM release
"fixes" the code to match the prose, every existing template moves by 2.5 mm.

## 5. DXF corners must be bulge polylines, not LINE + ARC

Recorded in SCM's `dxf_manager.py` from their own testing: Silhouette Studio
renders a visible sharp corner where a `LINE` entity meets an `ARC` entity.
The fix is a single closed polyline whose corner vertices carry a bulge
(group code 42), with the bulge for a 90° arc being `tan(22.5°) ≈ 0.414214`.

`template.write_dxf` emits exactly that: one closed `POLYLINE`/`VERTEX`
sequence per card, four bulges, `$INSUNITS` 4 and `$MEASUREMENT` 1 so the
units are not guessed at import. The same note also records that LINE+ARC
merges cards into one compound path, which is recoverable with Studio's
"Release Compound Path", and that the sharp corner is the real blocker.

**Not reproduced here.** This is SCM's finding, taken on trust, because
reproducing it needs Silhouette Studio, which is not installed (section 9).

## 6. Coordinate locking, verified by an independent renderer

**Method.** Generate the Letter portrait 3×3 proof, rasterise it with
`rsvg-convert -d 150 -p 150`, and measure the result.

**Result.** 1275 × 1650 px, which is exactly 215.9 × 279.4 mm at 150 dpi.

**Conclusion.** The SVG header (`width`/`height` in explicit `mm`, `viewBox`
carrying the same numbers unitless, so one user unit is one millimetre) is
being honoured by a renderer that knows nothing about this project. The usual
template-scaling failure — a pixel `width` with a disagreeing `viewBox`,
giving a 96/72 or 25.4/96 error — cannot occur with this header.

Separately, the built byte stream is decoded back and every card measured out
of it. On the Letter portrait 3×3 job: **card size error 0.031 mm, card
position error 0.023 mm**, against a 0.12 mm tolerance. One Silhouette unit is
0.05 mm, so the residual is the machine's own quantisation plus arc
flattening, which is deliberately held to 0.025 mm — half a machine step.

---

## 7. The protocol is recovered, not published, and is untested here

Every command byte in `cut_proxies/graphtec.py` was taken from
`fablabnbg/inkscape-silhouette`'s `silhouette/Graphtec.py`, which recovered
them from Silhouette Studio's own USB traffic. Each one carries its source
line in a comment. Nothing was inferred from a plausible-looking pattern.

What is known and reproduced:

* **Units.** 1 Silhouette unit = 1/20 mm = 0.05 mm. Checked against the
  reference's own worked examples: 8.5 in = 4318 SU, 11 in = 5588 SU.
* **Framing.** Every command is terminated with `ETX` (`0x03`). Escape
  commands (`ESC EOT` initialise, `ESC ENQ` status) carry no terminator.
* **Axis order.** `M` and `D` take **(y, x)**, not (x, y). This is the single
  easiest way to produce a transposed cut and it is tested directly.
* **Registration preamble.** `TB50,0` · `TB99` · `TB52,2` · `TB51,400` ·
  `TB53,10` · `TB55,1`, then `TB123,h,w,t,l` for a three-mark search or
  `TB124,…` for the Cameo 5 Alpha's four, then read and expect `b"    0\x03"`.
* **Origin shift.** After a successful scan the machine places logical (0,0)
  at the top-left mark, so every cut coordinate is sheet position minus the
  mark inset.

What is **not** known, and is passed through as a magic constant rather than
derived:

* `TB51,400` and `TB53,10` are described upstream as the mark length and
  width, but in units that were never pinned down. They are sent exactly as
  the reference sends them. If a scan fails on marks that are visibly correct,
  these two are the first thing to question.
* `TB99`, `TB55,1` and the trailing `SO0` have no documented meaning at all.
* The force and speed numbers are unitless machine indices. There is no
  published mapping to grams or mm/s and none was recovered, so the presets
  cannot be sanity-checked against a physical quantity.
* Upstream sends the pressure command **twice** with an acceleration change
  between, and notes that it does not know why. Reproduced verbatim.

**Status: never sent to a cutter.** `lsusb` shows no device under vendor
`0b4d` (Graphtec) or `3844` (Cameo 5 Alpha) on this machine. The stream is
built, decoded and measured; that is a claim about the bytes, not about what a
machine does with them.

## 8. The blade presets are extrapolations

*(Partly superseded by section 15: four of them now come verbatim from
Silhouette's own database, and the card presets are a much shorter
extrapolation from it than what is described below. The reasoning here still
holds; the starting point moved.)*

The recovered media table stops at `Tag 100 lbs (275g)` — force 20, speed 10.
The stock this suite prints on is 330 gsm, past the end of that table exactly
as it is past the end of the Canon's paper-weight range.

Every card preset in `cut_proxies/presets.py` is therefore marked
`origin: "derived"` and says "never cut" in its note, and both front ends
print `[NOT CUT]` next to it. The plain-paper and pen presets are `recovered`,
from the media table; one preset is `guide` — see section 11.

The reasoning behind the derived numbers, so it can be argued with: passes
beat force on heavy stock. One very high-force pass pushes the blade deep
enough to emboss the back of the card and to drag the sheet sideways in the
gap between the mat and the blade housing. Three passes at a moderate force
should cut 330 gsm more cleanly than one at the maximum, and are kinder to the
blade and the mat. The Card Maker guide independently gives the same tuning
order — passes, then force, then speed, then depth.

**When the Cameo 4 arrives:** cut the plain-paper preset first, then one card
sheet, look at the edge, and move the preset from `derived` to `measured` with
the date. Until then the label stays.

## 9. WinBoat: reachable, and Silhouette Studio now installed

**What is there.** The `WinBoat` container (`ghcr.io/dockur/windows:6.05`) is
running. Its guest agent answers on host port 47280 (`/health`, `/apps`,
`/metrics`, `/version`), RDP is on 47300, the host's home directory is bind
mounted at `/shared` and re-exported to Windows, and `/dev/bus/usb` is passed
through, so a cutter would be visible to the guest.

**What is not there.** *(Superseded 2026-11-07, later the same day.)*
Silhouette Studio was not installed at the time of writing. It is now, at
`C:\Program Files\Silhouette America\Silhouette Studio\Silhouette
Studio.exe`, which is one of the two paths `winboat.status()` already probed,
so detection needed no change. See section 13.

**A null result worth recording:** the WinBoat guest agent has **no execute
endpoint**. `/launch`, `/run`, `/exec`, `/info`, `/status` and `/processes` all
return 404. Commands cannot be run in the guest over HTTP. The only automation
route is RDP RemoteApp — `xfreerdp3 /app:program:…,cmd:…` — which is how
WinBoat itself starts applications, and which is fire-and-forget: there is no
way to read a command's output back. That is why `winboat.prepare()` writes a
PowerShell script that reports into a file in the shared folder instead of
trying to return anything.

**Not done on purpose.** `cut-proxies` does not install Silhouette Studio. It
is a signed, interactive, several-hundred-megabyte third-party installer, and
running one unattended into someone's VM is not a thing to do on their behalf
without being asked. `cut-proxies studio --prepare` stages a helper script
that checks for Studio, checks whether Windows can see a cutter
(`VID_0B4D`/`VID_3844`), lists the staged templates and writes a report back
to the shared folder. The install itself stays a decision the operator makes.

**Credentials.** The guest login lives in the user's own
`~/.winboat/docker-compose.yml` in plain text. `winboat.py` reads it only at
the moment it is needed, passes it straight to `xfreerdp`, and replaces it
with a placeholder in every string it returns for display. It is never
written to a file, a log line or an error message by this project. Do not
paste it into one.

## 10. Environment notes that will bite

* **There is no `pip` on this machine** and no `pyusb`. `python3 -m pip`
  reports no module named pip. Everything in `cut_proxies` therefore runs on
  the standard library alone — SVG and DXF are written by hand rather than
  with `svgwrite` or `ezdxf`, which is also why the DXF emitter had to
  reproduce the bulge encoding rather than call `add_polyline2d`. `pyusb` is
  imported lazily and its absence is reported as a clear message, not a
  traceback; it is the only thing standing between this and a registered cut.
* `rich` is optional, as in `print-proxies`; the CLI degrades to plain text.
* GTK 4.24 and libadwaita 1.10 are present, so the window uses `Adw.SpinRow`
  and `Adw.AlertDialog` without version guards.

---

## 11. The speed scale: two sources, and they disagree

**What was thought first, and was wrong.** The original presets used speed 3
to 8, from inkscape-silhouette's recovered media table, whose range is 1-10.
On reading the Silhouette Card Maker guide — which recommends force 25,
**speed 25**, depth 7 on a Cameo 5 — those looked like stale values from the
pre-Cameo-4 machines, and every preset was rescaled up to 20-25. A test was
even added asserting no preset carried a speed below 15.

**That was an over-correction, and the vendor's own data disproves it.**
Silhouette Studio 5.0.556.1 ships its material database as plain CSV
(section 15). Across all 26 materials and all ten cutter classes in that
file — `cameo`, `portrait`, `cc300-20` and `cc330-20` among them, which are
the Cameo 4 and Cameo 5 widths — the `(speed)` column takes only the values
**1, 3, 4, 5, 8 and 10**. It never exceeds 10, for any material, on any
machine class. The rescaling was reverted and the test replaced with one that
only checks the value is inside the machine's accepted range.

**Where that leaves it.** Two sources, both real:

| Source | Cardstock-family speed | What it is |
|---|---|---|
| Silhouette's `condition.csv` | 1–4 | the vendor's shipped defaults, per machine class |
| Card Maker cutting guide | 25 | community figures, measured on a Cameo 5 with an AutoBlade |

They cannot both be describing the same scale. Possible readings: the shipped
table is a legacy 1-10 scale that Studio rescales for newer machines; or the
Cameo 4/5 really do accept 1-30 and the vendor's defaults are simply
conservative. **No cutter has been attached here, so this is not resolved.**

The presets follow the vendor, because on 330 gsm black-core too slow costs
time and too fast costs the sheet. The guide's figures are kept as a separate
preset, `Cameo 4 starting point`, whose note says plainly that its speed
contradicts the vendor's. One thing the two sources *do* agree on: the guide's
"depth 7" is exactly Coverstock's 0.7 mm blade extension.

**What is worth taking from the guide regardless of the numbers:**

* **Tune in this order: passes, force, speed, depth.** Depth is a *ceiling* —
  it sets how far the AutoBlade can extend; force and passes decide whether
  it gets there.
* Cut a test sheet and, **without ejecting the mat**, lift a corner and look
  at the cut. Re-send the same job until it separates. The number of sends is
  your pass count.
* Maximum force or speed can make the machine skip. If it skips, power-cycle
  it so the carriage rehomes.

## 12. Cards crowd the registration marks, and the sensor notices

**The finding, from the Card Maker guide:** before sending a job, cover the
bottom-left card with a sticky note, "because this card sits close to the
bottom-left L registration mark, and the machine can confuse the two". Remove
it after registration, before cutting. Its `create_pdf.py --skip N` exists for
the same reason — leave the slot empty instead.

**Measured against our own layouts.** The offending card clears the mark's
corner zone by almost nothing:

| Layout | Cards | Cards crowding a mark | Smallest gap |
|---|---|---|---|
| Letter landscape, 10 mm inset | 8 | 3 | 1.26 mm |
| Letter portrait, 3.5 mm inset | 9 | 3 | 1.24 mm |
| A4 landscape, 10 mm inset | 8 | 3 | 1.16 mm |
| **Legal portrait, 10 mm inset** | **9** | **0** | — |

So this is not a quirk of one layout: every Letter and A4 layout that fills
the sheet puts three cards within ~1.2 mm of a mark. That is the gap the
Card Maker layout algorithm leaves by design — its corner rule only requires
clearance on *one* axis — and it is why the guide tells you to mask.

**What was done about it.** `regmarks.mask_hints()` reports every card within
`MASK_CLEARANCE_MM` (5 mm) of a mark's corner zone, naming the card, its row
and column, which mark it crowds and by how much. Both front ends print it as
an instruction, and the window tints those cards amber in the preview so they
can be picked out by eye. It is **not** a blocking error: masking is a
perfectly good answer, and the alternative is throwing away a third of the
sheet. `--skip N` leaves a slot uncut for the case where the slot was printed
empty instead.

The 5 mm threshold is a judgement call, not a recovered number — set well
above the ~1.2 mm that is known to fail and below anything that would flag a
layout with real room. Nothing published gives a figure.

**The practical consequence:** on this printer, **Legal portrait at the
standard 10 mm inset is the only layout that gets nine cards with nothing to
mask.** `cut-proxies layouts` now computes and prints that conclusion rather
than leaving it to be rediscovered, and filters out sheets the Canon cannot
feed — Tabloid would otherwise win on card count and be useless.

## 13. WinBoat: one RDP session, and nothing tells you

**The failure.** Three attempts to run a PowerShell helper in the guest over
RDP produced no output, no window and no error. The script was blamed first,
then the quoting, then the security mode. None of those was the cause.

**The cause.** Another tool on this machine was already holding an RDP
session to the same guest. Windows serves **one interactive session at a
time**, and a second connection neither queues nor fails loudly — it does
nothing, or it bumps the first one off mid-job. Nothing in `xfreerdp`'s
output says so.

`winboat.rdp_busy()` now checks for an existing client against the guest's
RDP port and names the process and the program it is running, and both
`run_script` and `open_in_studio` refuse with that message rather than
silently doing nothing. `cut-proxies status` reports the session as free or
held.

**Two other things that were needed to make it work at all:**

* **`/d:.`** — and *not* what was tried first. Without a domain,
  authentication against the guest's **local** account goes down the Kerberos
  path, fails with "Client's credentials have been revoked", and the
  connection is cancelled before any application starts. The obvious reading
  is "turn NLA off", and `-sec-nla` appeared to work: it exits 23 where the
  default exits 0 with `ERRCONNECT_CONNECT_CANCELLED`. **That was a
  misreading.** `-sec-nla` is not a flag xfreerdp3 accepts; it is rejected as
  an unexpected keyword and the client exits without ever opening a
  connection, so there is no connection error in the log — which looks like
  success if the only thing checked is the return code. The actual fix is
  `/d:.`, naming the local machine, with NLA left on. Two attempts were lost
  to the wrong reading before a RAIL session was seen to connect.
* **Launch through `cmd.exe /c powershell -File …`, not `powershell.exe`
  directly.** The RemoteApp argument string is parsed by Windows before
  PowerShell sees it, and a bare `-File \\host.lan\…` UNC path does not
  survive that reliably.

**The published ports move.** WinBoat picks the host ports when the container
is *created*, from the ranges in its compose file, so recreating the container
renumbers them: RDP went 47300 -> 47301 and the guest agent 47280 -> 47281
partway through this session, with the guest perfectly healthy throughout.
Anything that remembers a port number fails instantly against a closed one
and looks like the guest refusing the job. Everything here rediscovers both
from `docker inspect` on every call and never caches them, which is why the
move was invisible from this side. A sibling session hit the same thing from
the other direction and lost a run of print jobs to it.

A published port is still not a live one, so `port_open()` now checks before
connecting, and `Guest.why_not_ready()` names the specific reason — container
down, port not listening, agent silent, Studio absent — rather than one
"guest is not ready". Related: anything running *inside* that container can
vanish with a restart and nothing announces it.

**Telling "cannot ask" from "not there".** The guest agent answering `/apps`
is how Studio is detected. If the agent is down, the honest answer is "could
not determine", not "not installed" — the latter sends someone off
reinstalling software they already have. `Guest.studio_known` carries that
distinction and `studio_state` reports it.

**A null result:** the WinBoat guest agent still has no execute endpoint.
`/launch`, `/run`, `/exec`, `/info`, `/status` and `/processes` all 404. RDP
RemoteApp remains the only way to run something in the guest, and it is
fire-and-forget — nothing comes back. Every script this project stages
therefore writes its report into the shared folder instead of printing one.

## 14. The guest password no longer appears in the process list

Passing the credential as `/p:<password>` on xfreerdp's command line — the
obvious way, and what this did first — publishes it in `ps` output to every
user on the machine for as long as the session lasts. It was visible there.

FreeRDP 3 has `/args-from:fd:<n>`, which reads the whole argument list from a
file descriptor. `winboat._run_freerdp` now writes the arguments, password
included, into a pipe and passes only the read end, so the credential appears
in no command line, no file, and not in this process's environment. What the
function *returns* for display has the password replaced with a placeholder,
as before.

## 15. Silhouette Studio's material database, and what it confirms

Studio was installed into the WinBoat guest, and a read-only survey
(`cut-proxies studio --run-helper`) pulled its data files out through the
shared folder. Version **5.0.556.1**.

The find: `Resources/Resources/SPEC_S/condition.csv`, 325 rows of plain CSV,
is **Silhouette's own material cut-settings database** — the table behind the
material picker in the Send panel. Columns include `(force.pressure)`,
`(speed)`, `(nth_pass)`, `(blade_depth)`, `(tool.offset)` and
`cutter_class_id`, with one row per material per machine class.

Settings are **identical across every cutter class** for 22 of the 26
materials; only Fabric, both Heat Transfer variants and the Sketch Pen vary.

The cardstock family, which is what matters here:

| Material | Force | Speed | Passes | Depth |
|---|---|---|---|---|
| Cardstock | 20 | 4 | 1 | 0.3 mm |
| Adhesive-Backed Cardstock | 29 | 3 | 1 | 0.4 mm |
| Textured Cardstock | 30 | 4 | 1 | 0.3 mm |
| Glitter Cardstock | 33 | 3 | 2 | 0.6 mm |
| Chipboard | 33 | 1 | 2 | 0.5 mm |
| **Coverstock** | **33** | **1** | **2** | **0.7 mm** |

**Coverstock is the heaviest paper material Silhouette publishes a setting
for**, and force 33 is already the machine maximum, so for stock heavier than
Coverstock the only axis left is passes. That is what the `330 gsm
black-core` preset now is: Coverstock plus a third pass. It moved from "a
number argued from a 275 g table entry" to "the vendor's heaviest published
paper, plus one pass", which is a much shorter extrapolation.

**Two independent confirmations of recovered protocol values:**

* `(tool.offset)` takes exactly two values in the entire file: **18** for
  every blade material and **0** for the Sketch Pen. That is precisely
  inkscape-silhouette's comment on the `FC` command, quoted from robocut —
  "It is 0 for the pen, 18 for cutting" — so this column is `FC` in
  Silhouette units, and 18 SU is 0.9 mm, the standard blade. A value
  recovered by packet capture, confirmed by the vendor's own data file.
* `(blade_depth)` runs 0.1 to 0.9 mm, and depth_mm x 10 lands on the 0-10
  AutoBlade index the `TF` command takes. Coverstock's 0.7 mm is the "depth
  7" the Card Maker guide recommends for cardstock, from a completely
  different source.

**Not vendored.** `condition.csv` is Silhouette's, ships with their software,
and is not copied into this repository. `cut_proxies/materials.py` reads it
out of an installation the user already has — including the copy the guest
survey leaves in `~/cut-proxies/studio-extract/` — and `cut-proxies
materials` prints it or saves a material as a preset. Two tests assert that
every `origin: "silhouette"` preset still matches the vendor's file where one
is available, and skip where it is not.

**Still outstanding:** the `.studio3` file format, and whether Studio's
registration-mark geometry on screen matches what sections 3 and 4 derived.
Both need Studio driven interactively rather than surveyed, and the guest's
RDP session was handed back before that was done.

## 16. Double-sided sheets: the cut reads only the front

Proxy Press draws registration marks on front pages only — its export calls
`regMarks(L)` under `!back`, so a back page carries none. That is correct, and
it has two consequences worth stating rather than rediscovering:

* **The cut always registers from the front**, whichever way the sheet was
  turned over between passes. A sheet turned the wrong way is a printing
  fault; it does not send the blade anywhere unexpected.
* **A front/back misalignment is therefore not fixable from this side.** The
  blade cuts one outline through both faces. If the back art is off-centre
  after cutting, the cut was right and the print was wrong, and the standing
  calibration offset (section 17) cannot help — it would move both faces
  together. That belongs to the printer's duplex calibration.

Also worth knowing for anyone generating Cameo sheets: the `b-cameo` preset
is **landscape**, and a landscape page on short-edge-fed paper turns
top-to-bottom, not left-to-right. A sibling session found that Proxy Press's
printed duplex instructions assumed left-to-right unconditionally, which is
right for its portrait 3x3 presets and backwards for the landscape ones,
`b-cameo` included.

## 17. Two things added for a machine that has not arrived yet

A Cameo 4 is on order. Two features were built against it that are worth
recording because of what they assume.

**The settings-finding test sheet** (`cut-proxies testcut`). The guide's
method is to cut a test shape, lift a corner without ejecting the mat, and
re-send until it separates. `testcut` does the whole sweep in one pass: a
grid of small squares, each cut with its own force, speed, depth and pass
count, driven by `CutJob.plan_matrix`, which re-sends the settings block
between path groups. Verified by decoding a built stream: three distinct
`FX` values reach it, three squares at one pass and three at two produce
nine paths rather than twelve, and every square measures 15.00 mm.

Registration is **off** by default. A settings test does not need marks and
should not cost a printed sheet.

**The standing calibration offset** (`cut-proxies calibrate`). A fixed
correction for the error between where the sensor reports the marks and
where the tool lands, folded into the origin shift in
`CutJob._path_commands` and stored per machine.

The trap, and the reason there are tests for it: `verify` measures decoded
cards against the grid, so unless it subtracts the offset again first, a
correctly calibrated job reads as a position error of exactly the offset's
size and is refused. There are tests asserting the **first and the last**
card both move by the same amount, because an offset applied in the wrong
place scales across the sheet instead of translating it, and a first-card
test alone would not notice.

Neither feature has been exercised on hardware. They are in the same
category as sections 7-8: the bytes are checked, the machine is not.

## 18. A detector that has never fired is not a detector

Prompted by a sibling session, which found its CSP harness reporting a clean
page for a page that genuinely had a blocked inline script — the listener was
right, but the tail window was 1.5 s and the violation lands at 2.5 s. A
clean result from a broken check is indistinguishable from a clean run, and
it reads as a pass.

**Audited this project's own guards on the same principle.** Six had no test
coverage at all, and three existed *specifically* to catch a failure:
`mask_hints` (cards crowding a mark), `port_open` (published but dead port),
`why_not_ready` (which thing is missing), `studio_known` ("cannot ask" versus
"not there"), `rdp_busy` (another session holds the guest) and
`Transport.run` (registration reply mismatch). Every one had been verified
once by hand when written and never exercised again.
`tests/test_guards.py` now fires all of them deliberately, with synthetic
guest state and fake transports so the failure paths can be reached on
demand rather than only when something is actually broken.

**Then checked those tests can fail**, because a new test passing first time
is the same trap one level up. Five mutations, each disabling one guard at
its source:

| Mutation | Result |
|---|---|
| `mask_hints` never fires | caught — 5 failures, 1 error |
| `why_not_ready` skips the dead-port branch | caught — 1 failure |
| `Transport.run` stops checking the reply | caught — 1 failure |
| calibration offset silently ignored | caught — 3 failures |
| collision check never fires | caught — 4 failures |

All five caught; sources confirmed byte-identical afterwards. The
`Transport.run` mutation is the one that matters most: with the check
disabled, the suite still had to notice that a failed mark scan is followed
by cutting anyway.

**Then made the verifier certify itself, in the same run.** The sibling
session's follow-up sharpened the point in a way that mattered more than the
audit did: a separate proving run is not evidence about *this* run, because
it produces output indistinguishable from a blind check's. That applies far
more sharply here than to any CSP harness — `cut-proxies cut` prints a table
of all-OK checks and proceeds, and a verifier that always returned ok would
print exactly the same table. The operator cannot tell them apart, and the
table is the last thing between a wrong stream and a blade moving across
330 gsm card.

So `verify.liveness_failures` now corrupts the real stream four ways, each a
fault a named check exists to catch — the blade force, a cut position moved
1 mm, the registration preamble removed, the scan window moved 5 mm — and
feeds each back through the same `expectations_met`. Anything that comes
back clean means that check is not working, and `Job.blocked` refuses the
job regardless of what the table said. The check table now ends with:

```
  [ok  ] checks proven live         4/4 probes caught
```

**It found a real hole on its first run.** The force probe was not caught.
The Cameo 4 dialect sends the pressure command **twice** — upstream does it
and records that it does not know why — and `decode()` kept only the last
`FX` it saw, so a stream whose *first* force was wrong passed verification.
The decoder now records every force and every speed, and the check requires
all of them to agree with the profile. A probe written to test the tester
found a gap in the thing being tested, which is the best possible argument
for writing it.

**A probe can be caught by the wrong check.** The sibling session's next
point was the sharpest of the exchange: independent channels need one probe
each, but `expectations_met` reads a *shared* decode, so two checks can hide
each other while both appear probed. Tested it, and that is exactly what was
happening:

```
probe 'card position error'
  rejected: True | failing checks: ['closed loops']
  *** TARGET MISSED ***
```

The position probe shifted only the first `M` command. That moves a path's
start point without moving its end, so the loop stops closing and the
**closed loops** check objects first. The probe reported "caught", the job
was correctly refused, and `card position error` — the check that catches a
whole sheet cut 1 mm out of place — had still never been proven live.

Two fixes. The probe now shifts *every* coordinate of the first path, so the
card keeps its size and its closure and is simply in the wrong place. And
`liveness_failures` now requires the **named** check to be among those that
objected, not merely that something did; a probe rejected by anything else
is reported as "still unproven".

**Probe coverage extended from 4 checks to 8**, chosen as the ones whose
failure ruins a sheet or the mat: blade force, speed, card position, card
size, cuts inside the marks, cut paths, registration preamble, scan window.
Six isolate a single check. Two are genuinely entangled and say so — a cut
pushed off the registered area is also in the wrong place, and a deleted
path breaks the path count, the closure count and the position pairing at
once — which is allowed, because the requirement is that the target is
*among* the objectors.

Mutation-tested with a negative control:

| Mutation | Result |
|---|---|
| position check always passes | caught |
| size check always passes | caught |
| no-op control | clean, as it should be |

**A check that is already failing gives its probe a free pass.** The
sibling session found that two of its probes shared a channel, so one
probe's event could satisfy another's assertion. The analogue here is not
shared channels — each probe runs its own independent verification — it is
shared *state*: if a check is stuck failing on the unmodified stream, its
probe reports "caught" without having caused anything. Demonstrated by
forcing one check to always fail:

```
liveness reports: *** nothing missed - every probe counted it as a HIT ***
meanwhile the healthy stream itself: FAILS
```

Two controls now bracket the probes, as in theirs. A **leading** control
verifies the unmodified stream first: any check already failing there is
reported as unprovable rather than silently credited, and a baseline that
raises makes the whole run unattributable. A **trailing** control re-runs
the unmodified stream afterwards and requires the same answer, so no probe
can have left state behind. Probes whose target check does not run in this
mode are skipped as absent rather than counted missed.

**The leading control immediately found a real bug.** `--no-registration`
was unsendable and always had been. `expectations_met` asserted the
registration preamble, the mark type, the scan command and the scan window
unconditionally, and measured card positions against the mark origin. An
unregistered job has none of those — it cuts a blank sheet in sheet
coordinates — so six checks failed and the job could never be sent, however
correct it was. An advertised option that could not work. The verifier now
takes the registration mode: it asserts that *no* scan commands were sent,
and measures positions from the sheet origin.

That bug had been in place since the check table was written and no test
caught it, because every test built a registered job. It surfaced the
moment something asked "was this check red before I touched it?".

**The CSP measurements in this file were taken on a shared browser.**
A sibling session found that CDP port 9222 was held by *this* session's
Chromium — two stale instances, left behind because a `pkill` had been in a
command the harness blocked, so it never ran — and its own
`--remote-debugging-port=9222` launches had been silently failing to bind
and attaching to ours instead. For some stretch, two harnesses were driving
one browser, and it was firing deliberate violations into it.

Structurally the contamination could not reach this project's reads: each
run attaches to a tab it creates through `/json/new` and enables `Runtime`
and `Log` on that target only, so events from tabs another client opened do
not arrive. But that is an argument, not a measurement, and the whole point
of section 18 is that those are different things. The stale browsers were
stopped, 9222 handed back, and the measurement redone on a port this
session owns and asserts ownership of.

**And closed the loop on this project's own CSP claim.** Section 6's work
used a 9 s window, comfortably past the 2.5 s the sibling measured, but the
listener itself had never been proven. Forcing three violation classes
against the real page — external script, inline script, external image — all
three were reported, all three on `Log.entryAdded` with source `security`,
and **none** on `Runtime.consoleAPICalled`. A harness watching only the
console channel would report a clean page while the CSP silently blocks a
new image host or CDN script, which is exactly what Proxy Press's own
`AGENTS.md` warns about. Re-measured straight after: 0 events on the
untouched page, so the earlier clean result was genuine.

## 19. Re-measuring the CSP claim on a port we own

Section 18's CSP result was taken on a browser another session was also
driving (see above). Redone, with four things that have to hold before
"clean" means anything:

1. **The port is ours.** The caller checks the port is free, launches
   Chromium with a fresh profile, then asserts the process listening on it
   is that chromium with that profile. A debugging port is a shared
   resource and nothing about `--remote-debugging-port` guarantees you got
   it; a failed bind is silent and you attach to whoever did.
2. **The page actually loaded.** Asserted, not printed. The checker
   requires `layout`, `regMarks`, `regArm` and `cleanSettings` to exist
   before it will report anything.
3. **The listeners are live**, forced in the same run, one violation per
   channel, each carrying a marker unique to this session so a pass cannot
   be attributed to someone else's probe.
4. **Two no-op controls**, before and after, so a probe's effects cannot
   leak into another's window.

```
browser: Chrome/156.0.8078.73
page: / with layout/regMarks/regArm/cleanSettings present
  [ok  ] blocked image host     -> csp
  [ok  ] blocked inline script  -> csp
  [ok  ] console.error          -> console
  [ok  ] uncaught throw         -> exception

REAL EVENTS (0)
checks proven live        4/4
controls clean            2/2
PASS - clean, all channels proven live in this run
```

The conclusion is unchanged — the page with both sessions' changes is clean
— but it is now a measurement rather than a result that happens to look
like one.

**Guard 2 is not theoretical.** Pointed at `/healthz` instead of the app:

```
INCONCLUSIVE: probe ran but the app is not loaded (hasLayout false, url /healthz)
exit=2
```

Without it that run reports zero events and exits clean, because a page with
no application script has no violations to report. A harness that cannot
tell "nothing went wrong" from "nothing happened" will certify an empty
page. That is the same failure as a check that was already red being
credited to its probe (section 18), arriving from the other direction.

## 20. Refusing is not discriminating

A sibling session found its port-ownership check was a tautology — it asked
whether the browser it had just launched had the profile it had just given
it, which is always true, and never looked at what was actually listening.
Its fix came with a three-case proof, and the third case is the one worth
stealing: a guard tested only on the cases where it should refuse is passed
by a function hardwired to refuse. **A check wired to a constant scores two
out of three** unless something also tests that it accepts.

**Audited this project the same way, and found one.** `rdp_busy` — the
guard that stops this session walking into another's VM session, which has
already happened once here — had two tests, both negative: a free port
reads free, and `pgrep` being absent is survived. A function hardwired to
`return ""` passes both:

```
rdp_busy hardwired to always-free: 2 tests run, 0 failed
-> a guard that can never say "busy" passes the whole class
```

It had never been asked to say "busy". Added six cases covering both
answers and, importantly, the reasons: a held port is detected and names
the pid and program; a client on a *different* port is ignored; and a shell
wrapper that merely mentions `xfreerdp` is not counted as a client. That
last filter was written after observing both a `/bin/zsh -c …xfreerdp3…`
line and the real client in one `pgrep` output, and had never been
exercised — without it every launch would look self-blocked.

**Then made the audit permanent.** `tests/test_discrimination.py` hardwires
nine guards in *both* directions and requires each one's test class to
object each time:

| Hardwired | Caught |
|---|---|
| `rdp_busy` always free / always busy | yes / yes |
| `port_open` always open / always closed | yes / yes |
| `off_sheet` always clean / always dirty | yes / yes |
| `collisions` always clean / always dirty | yes / yes |
| `mask_hints` always silent | yes |

A check that can only answer one way now fails at least one direction, and
one wired to a constant fails both. It runs the other classes in-process,
which is unusual, but a proving run that does not happen alongside the
thing it certifies is not evidence about it — the same argument as section
18, applied to the test suite rather than to the code.

The file also proves it can fail: pointing a case at a class that cannot
notice its guard produces "that guard's tests do not discriminate".

## 21. Clean up at the start, not at the end

A sibling session's answer to the leaked-browser problem (section 19) is
better than mine and worth copying: switching `kill` to a graceful close
makes the shutdown tidier, but both sit on the *same line at the end of the
run*, so the failure that bit this session twice — the tooling blocks the
command, the output still reads as completion — leaks either way. Reaping
at the **start** is what survives it: last run's leak self-heals instead of
persisting.

**Audited this session's own leaks by looking at the system rather than the
transcript**, which is the only method that works here. Found one: a
`proxy-press-server.py` still listening on 8744 from the re-measurement in
section 19, hours after I believed it stopped. Stopped it through the task
system rather than a shell kill, and confirmed by `ss` that the port was
released. The Chromiums were genuinely gone — `Browser.close` had worked.

**Applied the pattern where it matters here.** This project's equivalent
leak is an `xfreerdp` client holding the guest's single RDP session, which
is precisely the failure that nearly cost a sibling session several
thousand print jobs. Three changes:

* Our launches now carry `/wm-class:cut-proxies-guest`, so a stale session
  of ours is distinguishable from another tool's live one. Without the tag,
  `rdp_busy` could only say "something holds the guest", and the two cases
  want opposite responses.
* `release_our_sessions()` reaps **only** sessions carrying that marker, and
  runs at the start of every launch path.
* `cut-proxies studio --release` does it on demand.

Checked against the real situation on this machine while a sibling's sweep
was running:

```
rdp_busy:           pid 1608683 running cmd.exe (not ours) already holds ...
our stale sessions: none - the holder is not ours, correctly invisible
studio --release:   That one is not ours and is left alone.
```

**The boundary is the dangerous part, so it is tested in both directions.**
`release_our_sessions` terminates processes; if it could see a foreign
client it would kill a sibling's work mid-sweep. Nine tests cover it,
including the real observed `canon-byte-sweep` command line being invisible
to it, and `tests/test_discrimination.py` hardwires the guard to claim a
foreign session is ours and requires the suite to object.

**What none of this fixes.** If the reap line itself is blocked, the leak
persists to the next run. It converts "leaks indefinitely" into "leaks
until next time", which covers the real case, but there is no immunity from
inside the harness: every probe in sections 18-20 assumes the harness ran
what it reported running, and twice here it did not while reporting
success. Looking at system state directly is the whole answer, and the
start-of-run reap is just that, done automatically at a point where it
still helps.

## 22. A bound set at launch is the only defence that does not depend on cleanup

The most useful structural point in this whole exchange, and it came from a
sibling session checking why it had *not* leaked anything. The answer was
not that its cleanup worked — plenty of its commands had exited 144 before
reaching their `pkill`, the same failure that bit here twice. It was that
every server it started was wrapped in `timeout N`, so each died on its own
schedule whether or not the cleanup line ever ran.

That is the one protection in sections 18-21 that does not rest on the
assumption underneath all the others: it is set **by the thing being
launched, at launch**, and survives the reap, the close, and the rest of
the session never happening.

**Applied here.** `cut_proxies/winboat.py` launched `xfreerdp` through a
bare `Popen` with no bound at all. A hung client holds the guest's single
RDP session indefinitely, which is exactly the failure that nearly cost a
sibling session a multi-thousand-job sweep. Every launch is now wrapped:

| Launch | Bound |
|---|---|
| helper script (`run_script`) | 600 s — minutes, not hours |
| interactive Studio (`open_in_studio`) | 4 h — long, but not unbounded |

The reap from section 21 still runs at the start, but it is now the second
line of defence rather than the only one.

**The same message found a second bug: matching a mention, not a process.**
The sibling's `pgrep -af proxy-press-server.py` matched its own invocation,
and the `[p]attern` trick failed too because the wrapper's command line
contained the literal bracketed pattern. Checking ours, the filter in
`rdp_busy` and `our_stale_sessions` was a prefix heuristic — "does the line
start with `/bin/`" — and it passed a `python3 -c "…xfreerdp3…"` wrapper as
a live client:

```
current filter sees a python wrapper as a client? YES - BAD
```

A reaper that matches a mention can target a shell about to launch the
client, a `timeout` wrapper, or itself. `_is_rdp_client` now identifies the
**executable actually being run** — the first token whose basename begins
with `xfreerdp`, allowing one `timeout N` in front because that is how we
launch — and is tested on all five shapes.

**And a third, in the tests rather than the code.** The two new
launch-bound tests took 2.3 s and 2.0 s, because `run_script(dry_run=True)`
still calls `status()`, which shells out to `docker inspect`. That is not
just slow: this tier is required to be offline, and it had been talking to
Docker. `status` is now stubbed in those tests, and the suite is back to
0.99 s for 217 tests with a measured **zero** Docker invocations — measured
by spying on `subprocess.run` for the whole run, rather than asserting it.

## 23. The method, separated from the incidents

Sections 18-22 are five incidents. The method under them is shorter than
the incidents and is the part that transfers, so it is written out here
rather than left to be inferred.

**Check that the thing ran, not that the outcome looks right.** Every
failure in those sections had the same shape: output indistinguishable
between "working" and "absent". A verifier that always returns ok prints
the same table as one that works. A check already failing is credited to
its probe. A control passes because the code under test crashed before
reaching it. A clean CSP result comes from a page that never loaded. In
each case the outcome was correct-looking and the mechanism was not
running.

**The tells are almost never in the result.** They are in the margins:

* *Timing.* Two tests taking 2.3 s in a suite that runs in 1 s was the only
  visible sign they were shelling out to Docker — the assertions were
  green and the thing they asserted was true. Nothing about the result said
  the tier had stopped being offline.
* *Counts.* "8/8 probes caught" is only meaningful beside "which check
  caught each one". Six of my eight probes isolate one check; two are
  entangled and say so. A count alone hid a dead check behind a live one.
* *System state.* `pgrep` and `ss` contradicted the transcript twice. The
  transcript said the cleanup ran. It had not.

**Attention does not substitute for mechanism.** Both sessions in this
exchange kept finding these failures in each other's work *and then
committing the same class of error in the fix*, thousands of words in, with
the failure mode explicitly in mind. That is not carelessness; it is what
"invisible by construction" means. The answer is a check that can tell
absent from working — a probe that must fire, a control that must be clean,
a guard hardwired both ways — not more care.

**One protection does not rest on the harness.** Everything above assumes
the harness ran what it reported running, and twice here it did not while
printing success. A bound set at launch (`timeout`) is the only exception:
it is applied by the thing being launched and holds when nothing else does.

## 24. Two paths that share an assumption are one path

I claimed the SVG and DXF importers "agree to 0.000 mm" as a cross-check,
and reported it that way. A sibling session asked the right question: do
the two paths compute independently, or serialise one model? And flagged
the exact agreement as the tell — two genuinely separate computations of a
physical quantity rarely land on *exactly* zero difference, because they
would have to share their rounding.

Checked, and the claim was overstated. The two paths share two things:

* **Both files are written by `template.py` from the same rectangles.** The
  literal corner coordinates are the same numbers twice. Their agreement
  shows the writer/reader pairs round-trip; it says nothing about whether
  63 mm is 63 mm.
* **Both arc flatteners choose their segment count with the same formula**,
  `ceil(sweep / 2·acos(1 − tol/r))`, so they sample identical angles and
  land on identical points. That is why the difference is exactly zero,
  and the exactness was the evidence of dependence rather than of
  accuracy.

What the comparison *does* establish is narrower and still worth keeping:
the SVG endpoint-to-centre arc conversion and the DXF bulge-to-centre
conversion are genuinely different derivations, and they place the arc in
the same position. That is a real cross-check of two bits of arithmetic.
It is not a confirmation of absolute scale.

**Where absolute scale actually comes from.** One measurement in this
project does not pass through its own arithmetic: `rsvg-convert` renders a
template to PDF and the result measures 1275 × 1650 px at 150 dpi, exactly
Letter (section 6). A third-party renderer, a different codebase, an
outside answer. Everything else is internally consistent, which is a
weaker thing.

The test is renamed and its docstring now says all of this, and a further
test asserts the outside measurement still exists — because if it were ever
deleted, the SVG/DXF agreement would be the only thing left that *looks*
like independent confirmation of size.

**The general rule**, which belongs beside the others in section 23: a
second path is only evidence if it does not share an assumption with the
first. Ask what the two have in common before counting them as two, and
treat exact agreement as a reason to look rather than a reason to relax.

## 25. SVG viewport size does not set coordinate units (2026-11-08)

Continuing the Linux cutting workflow, inspection found that `load_svg`
interpreted bare coordinates as millimetres whenever both page dimensions
were present and there was no viewBox. A path ending at (96, 96) therefore
ended at (96 mm, 96 mm), rather than (25.4 mm, 25.4 mm).

SVG's initial user coordinate system uses pixels regardless of physical
viewport dimensions: https://www.w3.org/TR/SVG11/coords.html#InitialCoordinateSystem.
The importer now retains its CSS-pixel scale in this case and reports it.
Page dimensions remain physical dimensions; viewBox handling is unchanged.

The regression test failed before the fix for all four viewport spellings
(mm, cm, inches and px), then passed. The full offline suite passed: 307
tests, zero tests asserting nothing. The generated eight-card dry run also
passed all checks and eight liveness probes. This establishes software
behaviour only; no cutter was connected and no hardware cut was attempted.

## 26. Live Studio DXF import comparison (2026-11-08)

WinBoat was reachable outside the sandbox. A sandbox-denied Docker socket
had previously made status report the guest as stopped; that was not evidence
of guest state. Studio 5.0.556.1 was launched through the existing guarded,
600-second RDP helper. No print or cut was sent.

Windows UI Automation exposed only unnamed panes (nine at the home screen,
15 after import), not numerical controls. Screen capture did work. Opening
our generated Letter landscape eight-card rounded DXF via Ctrl+O produced
eight rounded cards in Studio. Ctrl+A showed a group measurement of
10.356 by 7.175 inches. Linux reads the same file's bounds as
255.778 by 177.2073 mm, approximately 10.070 by 6.977 inches. Studio's
individual-object toolbar showed W 2.551 and H 3.562 inches, also larger
than a 63 by 88 mm card. The group discrepancy is approximately 2.84%.

This is an observed import discrepancy, not a diagnosed unit conversion.
Import preferences, auto-sizing and document placement have not yet been
isolated. Do not compensate in the writer on this evidence. The screenshot
shows a square page/mat rather than the source Letter landscape page, so
page metadata preservation is also unproved. Rounded corners are visible;
precise arc fidelity cannot be inferred from the screenshot.

Local evidence (not vendored): `guest-staging/dxf-probe/report.txt` and
`screen.png`, and the source DXF in `guest-staging/studio-comparison/`.
`tools/probe-studio-dxf.ps1` preserves the probe with parameterized guest
paths; that generalized version has not yet been rerun. It must be launched
through `winboat.run_script`, with its normal guard and timeout. It opens a
new document and selects all; it does not save, print, or send a cut.
The next differential experiment is to record Studio's DXF import
preferences and repeat with a known 100 mm square, changing one preference
at a time. Native `.studio3` decoding remains outstanding.

## 27. Credential-pipe RDP sessions were invisible to the busy guard

2026-11-08: before the next Studio experiment, an actual `xfreerdp3`
process had an established loopback connection to WinBoat port 47301,
but `status` reported the session free. Its argv contained only
`/args-from:fd:3`, so the existing `/v:` search could never see it.
This is a consequence of the credential protection in section 14.

`rdp_busy` now checks established socket peers for real clients with
opaque arguments, matching the exact process id and guest loopback port.
If socket inspection cannot run, it refuses the launch. It never reads
the credential pipe and never treats unknown ownership as our marker.
`our_stale_sessions` remains restricted to explicitly marked clients;
the unidentified live session was not killed or replaced.

The connected-client regression failed before the fix. Seven offline tests
cover connected/other-port/other-pid/wrapper cases, local-versus-peer port,
inspection failure, and exclusion from reaping. The existing discrimination
mutations now also exercise this test class. All 314 tests and the assertion
audit pass; the generated job dry run passes. The Canon instance was told
about the detector gap and is also holding off on VM launches.

A 100 mm square DXF is staged locally for the next import comparison. Its
Linux-decoded bounds are (20,177) to (120,277) mm on a 210 by 297 mm page.
It has not yet been opened in Studio: the unidentified session currently
holds the guest. The 2.84% observation in section 26 remains unresolved.

## 28. Card Maker cutting guide alignment & Studio workflow integration (2026-11-09)

The cutting workflow and presets were audited against the published Silhouette
Card Maker cutting guide (https://alan-cha.github.io/silhouette-card-maker/tutorial/guide/).

1. Presets and blade settings:
   - Added `Card Maker reference (cardstock + laminate)`: force 33 (clamped
     to Cameo 4 maximum; Cameo 5 Alpha accepts up to 40), speed 25, depth 7,
     4 passes. Origin is `guide`.
   - Exposed `Card Maker starting point` (force 25, speed 25, depth 7, 1 pass)
     and retained `Cameo 4 starting point` as an alias for backwards compatibility.
   - Updated tuning rules in documentation and GUI to reflect the guide's
     priority: Passes -> Force -> Speed -> Depth. Added guide troubleshooting
     notes (reduce force if laminate edges ripple; reduce speed if machine skips).

2. Template card-skipping fix:
   - Inspection of `template.write_svg` and `template.write_dxf` revealed that
     both functions looped over `grid.cards` without consulting `skip`, meaning
     slots intentionally left empty to prevent optical sensor mark collisions
     still produced cut paths in exported SVG and DXF files.
   - Both functions now accept `skip`, and `job.write_templates` passes
     `job.skip`. Skipped cards are omitted from both exports, proven by unit tests.

3. Silhouette Studio integration & operator guidance:
   - Added `--via studio` to `cut-proxies cut` to build and verify the job,
     stage the DXF in the WinBoat guest share, launch Studio via RDP RemoteApp,
     and output an operator checklist covering page setup, registration marks,
     bounding box dimensions for Ctrl+A verification, and blade settings.
   - Updated `cut-proxies studio` to generate and stage the template when run
     without a file argument, and added `--app-only` to launch Studio blank.
   - Updated ProxyPress guidance to reflect the new 1-click "Set up for
     Silhouette Cameo" button on the Cutting tab and the 3.2 mm MTG corner radius.

## 29. Reverse engineering baseline and native-file headers (2026-11-09)

Executed the briefing's initial audit: all 317 offline tests pass, the
assertion audit reports zero tests asserting nothing, and the generated
eight-card dry run passes all checks and eight liveness probes. Host status
reports no Silhouette USB cutter, no pyusb, and no usblp node. No hardware
job was sent.

Ran `tools/survey-studio-re.ps1` inside WinBoat through `winboat.run_script`,
using its session guard, credential pipe and 600-second launch bound. The
first sandboxed attempt could not see the Docker socket; an approved launch
outside the sandbox succeeded. The report, rather than the launch return,
confirms execution: `complete: true`, no reported errors, start
`2026-11-09T12:55:07.3585406-08:00`, finish
`2026-11-09T12:55:08.7929771-08:00` (guest timestamps).

Observed executable: Studio file version **5.0.556.1**, product version
`V5.0.556.001`, SHA-256
`4871EDB71FB28B02593850006992277E69E058D0A385B0AC6402F63B9514A47B`.
The briefing's 4.5.815 version is not the installed executable surveyed.
The discovered share was `\\host.lan\Data`, mapping the host home directory;
the guest staging path was derived using `winboat.guest_path`.

The Studio installation contained **39 `.studio3` samples**, from 38,305
to 2,530,095 bytes. Every recorded first-64-byte header begins with the
13 ASCII bytes `silhouette05;`. Thus these files do not start with a normal
ZIP or SQLite header. This says nothing yet about embedded containers,
compression, obfuscation, field offsets, units or cut-setting semantics.
No native parser or generator has been established. No additional samples
were found in the scanned Public Documents and user Documents directories.

Five known paths for tshark, dumpcap, USBPcapCMD and USBPcap.sys were absent;
this is not an exhaustive installed-tool search. The guest USB inventory
listed a root hub and an input device, with no Cameo. Differential USB
capture remains outstanding.

Local evidence: `guest-staging/re-inventory-2026-11-09/inventory.json`,
SHA-256 `fc7c4d39db09e281636febefb5c100eaba6d61456513b2f6f782dc0fe146d5f9`.
It contains sample paths, sizes, SHA-256 hashes and 64-byte headers, not
copies of the proprietary documents. The survey does not launch Studio or
change its documents. `docs/REVERSE_ENGINEERING.md` records the target audit
and controlled experiments to run next. Existing `TF`, `J`, `FE`, `FF`
and `FC` support must be distinguished from still-unproved AutoBlade
mechanics, tool compatibility and Studio UI semantics.

## 30. UI, send-path and current Proxy Press audit (2026-11-09)

The program is now named **Silhouette Control Center**, with command
`silhouette-control-center` (the `cut-proxies` entry point remains an alias),
desktop/app/icon ID `org.printing.SilhouetteControlCenter`, and locally
installed scalable plus 16–256 px raster icons. The live GUI audit confirms
that GTK's icon theme resolves the new ID. The old desktop launcher was
retired to a `.desktop.disabled` backup after the replacement was installed.

The owner requested removal of the distinct Proxy Press pattern; sections 3
and 12 describe historical assumptions, not current requirements. Current
Proxy Press uses standard three/four marks, and its Cameo mode is eight-up
landscape. Read `DEFAULTS`, the Cameo mode/apply handlers, `layout()` and
`regArm()` from the local `../proxypress/proxy-press.html`, SHA-256
`3d8b4d9d669581755f04a9af72038da537356c6fadc21b6050e6b70d3085892e`.
Executed the actual layout/arm functions using Node, separately from the
Python implementation. The MTG presets match card positions and arm lengths:
7.25 mm on Letter, 8.75 mm on A4, with 3.2 mm corners and 0.625 mm bleed.

The user's actual `Downloads/cut-lines.svg` contains eight closed rounded
rectangles on an explicit 279.4 × 215.9 mm viewport with a matching viewBox,
no marks, and no importer warnings. Its vertices match the Letter preset
within 0.001 mm. The imported job verifies and catches all eight liveness
probes. Independent rendering gives 1650 × 1275 px at 150 dpi. This is
software/size evidence, not a sensor or cutting result.

Audit regressions found that both send front ends discarded calibration
while reconstructing their plans, and that the GUI unconditionally turned
registration on. Jobs now retain the exact verified write/read steps and
both front ends send those. Five actual-send/fake-transport regressions
failed before the fix and pass afterward. GUI confirmation now retains the
reviewed job and checks it again at confirmation; constant-allow/refuse
mutations are part of the normal discrimination test.

Other reproduced fixes include missing Gio imports in both Open buttons,
imported shapes exporting as bounding rectangles, and radius overrides
disappearing from export. The writer proof was independently rasterized to
1275 × 1650 px for portrait Letter at 150 dpi. The GUI now displays uploaded
PDF artwork at physical size under the paths, has a compact default summary
and explicit Advanced details checkbox, fills an adjustable pane instead of
a narrow preferences clamp, and offers persistent colorblind styling with
blue/orange, dashed failures and OK/! text. Stale PDF results have a tested
identity guard, including constant-accept/reject mutations.

`310 gsm black-core` is an owner-requested **derived, never cut** starting
guess: force 33, speed 1, depth 7, two passes, based on Coverstock values.
It is not promoted to measured or attributed to the vendor for this stock.

See `docs/UI_FUNCTIONALITY_AUDIT.md` for scope, reproduction evidence and
remaining issues. In particular, test-cut matrices still lack the per-job
proof, certain SVG/DXF constructs import incorrectly, and measured PDF
problems do not yet block Cut. No hardware was used to validate these changes.

## 31. Proxy Press per-sheet SVG handoff (2026-11-10)

**Method.** Executed Proxy Press's actual layout, registration and cut SVG
functions with Node, imported their output with the cutter's SVG importer,
and built verified jobs with zero and nonzero blade-to-sensor offsets.
Letter and A4 used 1, 8, 9 and 16 cards; a separate case exercised unequal
printer X/Y scaling and translation. Local fixtures preserve these exports
for standalone offline testing.

**Result.** All tested default sheets, including partial final sheets, import
and pass stream verification. Compensated SVG bounds match the transformed
card positions. Proxy Press previously left rounded-corner radii unscaled;
the export now scales both radius axes. The new test checks those axes.

**Limits.** Node geometry checks do not exercise the browser, PDF renderer,
printer or cutter. The browser harness could not establish a local session
under the execution environment's socket restrictions. No hardware job was
sent. PDF inspection cannot detect scaling introduced by a physical printer.
Arbitrary asymmetric printer compensation transforms registration marks as
well as art; nominal cutter mark controls do not fully express that transform.

## 32. Declared SVG page and registration must agree (2026-11-10)

**Method.** Loaded a one-card Letter SVG from Proxy Press, selected A4 in
the job builder, and retained the printed 7.25 mm mark arm. The paths fit
and the old verifier passed, despite using A4 registration coordinates.
Added an explicit SVG viewport field distinct from artwork bounds. For
registered jobs the shared builder now blocks mismatches over 0.1 mm in
either axis. SVGs without declared dimensions and DXFs keep their previous
behavior; their extents do not establish a physical page.

**Result.** Matching Letter and A4 exports pass; wrong size and orientation
are blocked, including partial sheets. CLI tests confirm refusal before
transport opens. The discrimination suite forces the guard to always allow
and always refuse and requires its tests to catch both mutations. Nonzero
blade-to-sensor offsets still pass the existing export tests.

**Browser validation.** The initial probe fixture was classified as a
placeholder and triggered art lookup. It now stores generated local artwork
as an upload and removes it afterward. The corrected browser probe passed
for Letter/A4 with 1, 8, 9 and 16 cards, including duplex PDF exports and
sheet selection. All four console/CSP channels proved live. Rasterized PDF
front-page registration and imported cut SVGs passed the cross-project test.
This supersedes section 31's browser execution limitation. No print or cut
was sent; digital geometry checks make no claim about physical output.
