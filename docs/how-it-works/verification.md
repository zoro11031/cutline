# Verification

Before any job is sent, Cutline decodes the stream it built and measures it
against what was asked for. A table of passing checks proves nothing if the
checks could not fail, so every run also proves its checks are working.

## What is checked

`verify.decode` reads the built stream back into commands, and
`verify.expectations_met` checks it: the blade settings at every cut, every
card's position and size on every pass, closed loops, path counts, cuts
inside the registered area, the registration preamble and the scan window.
For a job without registration it checks instead that no scan command was
sent, and measures from the sheet origin.

On the Letter portrait 3×3 job (2026-11-07) the decoded cards came back with
a size error of 0.031 mm and a position error of 0.023 mm, against a 0.12 mm
tolerance. One Silhouette unit is 0.05 mm, so the residual is the machine's
own step plus arc flattening, which is held to 0.025 mm (half a step).

## The checks prove themselves on every run

`verify.liveness_failures` breaks the real stream several ways, each a fault
one named check exists to catch, and runs each broken copy through the same
checks: a wrong force, a wrong speed, a card moved, a card resized, a cut
pushed outside the marks, a path deleted, the registration preamble removed,
the scan window moved, and the last cut path shifted. If a broken copy comes
back clean, that check is not working and the job is refused. It runs inside
every build; it is not a separate command, because a proving run made
separately prints exactly what a blind check prints and says nothing about the
job in front of you.

Three rules came out of building it:

- **A probe must be caught by the check it names.** The first position probe
  moved only a path's first point, so the loop stopped closing and the
  closed-loops check objected first. The job was refused, the probe counted
  as caught, and the position check had never been proven. Probes now move a
  whole path, and the named check must be among those that object.
- **Two controls bracket the probes.** The unmodified stream must pass before
  them, because a check that is already failing would credit every probe
  aimed at it, and must give the same answer after them, so no probe leaves
  state behind. The leading control found a real bug the day it was added:
  `--no-registration` had never been sendable, because six checks assumed
  registration.
- **Every pass is measured.** The verifier once measured only the first
  pass's cards; a shift in a later pass went through. Every path is now
  measured against its card on every pass, and one probe shifts the final
  path.

The self-check found a hole on its first run (2026-11-07): the Cameo 4 sends
the force command twice, the decoder kept only the last, and a stream whose
first force was wrong passed. Every force and speed is now checked.

## Guards and their tests

The test suite holds the checks to the same standard:

- `tests/test_guards.py` fires every check that refuses a job or warns the
  operator. Six such checks had been verified once by hand and never again.
- `tests/test_discrimination.py` hardwires each guard to always refuse, then
  to always allow, and requires its test class to fail both times. A guard
  tested only where it should refuse is passed by a function that always
  refuses.
- `tools/audit_tests.py`, run at the end of `./run-tests.sh`, counts the
  assertions in each test and fails on any that made none, usually a loop
  that ran zero times.

When a guard is added, its test is written to fail first, and the guard is
then broken at its source to confirm the test notices.

## Absolute scale

Most of these checks are internal: Cutline's arithmetic agreeing with itself.
One measurement is not. `rsvg-convert`, a renderer that knows nothing about
Cutline, rasterises the Letter proof to exactly 1275 × 1650 px at 150 dpi,
which is 215.9 × 279.4 mm. The SVG header sets `width` and `height` in `mm`
with a matching unitless `viewBox`, so one user unit is one millimetre. A test
keeps that outside measurement in place.

The SVG and DXF writers agreeing to 0.000 mm was once reported as a scale
check. It is not one: both are written from the same rectangles, and both arc
flatteners choose their segments with the same formula, so exact agreement is
evidence that they share an assumption. What it does confirm is narrower: the
SVG arc conversion and the DXF bulge conversion are different derivations that
place each arc in the same spot.
