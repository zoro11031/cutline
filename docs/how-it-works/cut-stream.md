# The cut stream

Silhouette cutters speak a Graphtec command language (GP-GL). Nothing about it
is published by the vendor. Every command byte in `graphtec.py` comes from
[inkscape-silhouette](https://github.com/fablabnbg/inkscape-silhouette)'s
`Graphtec.py`, which recovered it from Silhouette Studio's own USB traffic, or
from a capture of Studio made here, and each carries its source in a comment.
Nothing is added because it looks plausible.

## The basics

- **Units.** One Silhouette unit is 1/20 mm (0.05 mm). Checked against the
  reference's worked examples: 8.5 in = 4318 units, 11 in = 5588.
- **Framing.** Commands end with `ETX` (`0x03`). Escape commands (`ESC EOT`
  initialise, `ESC ENQ` status, `ESC NAK` tools) carry no terminator. Replies
  end with `ETX`.
- **Axis order.** `M` (move) and `D` (draw) take **y, x**, not x, y. It is the
  easiest way to cut a transposed sheet, and it is tested directly.
- **Origin.** After a successful mark scan the cutter puts (0, 0) at the
  top-left mark, so every cut coordinate is its sheet position minus the mark
  inset.

## Values passed through without an explanation

Some values are sent exactly as the reference sends them and are labelled as
unexplained rather than given a meaning:

- `TB51` and `TB53` are described upstream as the mark length and width, in
  units never pinned down. Studio's capture sends `TB51,161` for an 8.0264 mm
  arm and `TB53,20`.
- `TB99`, `TB55,1` and the trailing `SO0` have no documented meaning.
- Force and speed are unitless indices. No mapping to grams or mm/s has been
  published or recovered.
- Upstream sends the force command **twice**, with an acceleration change
  between, and notes that it does not know why. Cutline does the same, and
  the verifier checks both (see [Verification](verification.md)).

## Registration

The mark search, as Studio sent it to a Cameo 4 (firmware 1.20) on
2026-11-17, captured on the USB bus:

```
TG1  FN0  TB50,0  TB99  TB52,2  TB51,161  TB53,20  TB55,1
TB123,3918,5188,118,118  TB99
```

`TB123,h,w,y,x` searches for three marks; `TB124` is the Cameo 5 Alpha's
four-mark search. The cutter replies `"    0" ETX` when it finds the marks and
`"    1" ETX` when it does not, about 14 seconds after the search.

Studio's capture differed from Cutline's earlier sequence in four ways: the
search started at (118, 118) units rather than (0, 0), a trailing `TB99`
followed the search, the setup went out in two writes, and Studio asked for
registration *before* sending blade settings, then sent the clip bounds after
the success reply. Cutline now sends that sequence verbatim for this geometry.
On 2026-11-17 a registration-only run from Linux on the same printed sheet
got the marks-found reply in 14.51 s. Several things changed together, so no
single byte is known to be the cause. The capture is
[`cameo4-studio-registration-2026-11-17.tsv`](../cameo4-studio-registration-2026-11-17.tsv),
and `tests/test_send.py` compares Cutline's setup writes against it.

Studio's own registration failed twice on that sheet and then succeeded, with
the **identical** search command each time. The optical cause of those two
failures is unknown.

## Tools

`ESC NAK` asks which tool sits in each holder. The reply is two numbers, for
holder 1 and holder 2. Names follow inkscape-silhouette's table, except 255,
which is named as Studio displays it:

| Code | Tool |
|---|---|
| 0 | Empty |
| 1 | Ratchet blade |
| 2 | AutoBlade |
| 3 | Deep-cut blade |
| 4 | Kraft blade |
| 5 | Rotary blade |
| 7 | Pen |
| 255 | No tool detected |

Observed on a Cameo 4: ` 2, 0` with an AutoBlade in holder 1 (USB,
2026-11-18); ` 255, 4` and ` 1, 4` alternating from one poll to the next with
a ratchet blade in holder 1 and a Kraft blade in holder 2 (Bluetooth,
2026-11-21). The alternation also appears in Studio's own polling, so it is
the cutter's detection, not the connection. Studio showed it as "No tool
detected".

Both windows read the tools once per connection, when the cutter or endpoint
changes, and on Check Status, never during a job. Blade Depth (`TF`) is an
AutoBlade command: when holder 1 reports another tool, the depth is set back
to manual and the operator is told, and a job that still carries `TF` for a
manual blade is refused at the transport (`transport.check_autoblade`).

## Passes and groups

A job can carry several groups of paths, each with its own force, speed,
depth and passes. Between groups the settings block is sent again
(`graphtec.plan_matrix`).

**Staged passes (2026-11-20)** use the same mechanism, with no new command
bytes. With "Lighter first passes" on, Passes stays the total: 8 passes with
2 light ones cuts passes 1–2 at the light force and speed and passes 3–8 at
the full settings. `actions.stage_passes` splits each group into a light group
and a full group over the same paths, and `actions.light_pass_problem` refuses
light passes that leave no full pass. A Letter eight-up job built this way
decodes to 64 card paths, the first 16 light and the last 48 full. That a
lighter first pass stops a laminated edge lifting is a user's report, not a
measurement made here.

## The settings test sheet and the calibration offset

`cutline testcut` cuts a grid of small squares, each with its own force,
speed, depth and passes, in one job. Registration is off by default: a
settings test does not need marks and should not cost a printed sheet. The
windows' Dial In a Material does the same one square at a time, with two sets
of cut settings (A and B) to compare, and records which one cut each pass.

`cutline calibrate` stores a fixed offset between where the sensor finds the
marks and where the blade lands, per machine. The verifier subtracts it again
before measuring; otherwise a correctly calibrated job reads as a position
error the size of the offset and is refused. Tests check that the first and
the last card move by the same amount, since an offset applied in the wrong
place grows across the sheet instead of moving it.
