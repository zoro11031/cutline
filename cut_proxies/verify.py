"""Decode a built cut stream and check it against what was asked for.

This is the same bargain Canon Suite makes with the printer: the job is
assembled, then taken apart again and compared with the profile, and if any
of it disagrees nothing is sent.  Card is expensive and a cut is destructive
in a way a print is not - a print on the wrong settings is a wasted sheet, a
cut on the wrong coordinates is a wasted sheet *and* a blade dragged across
the platen.

The checks that matter are the geometric ones.  Force and speed landing
correctly only proves the stream was built; measuring every decoded card back
to 63.00 x 88.00 mm proves the whole chain - layout, mark origin shift, unit
conversion, (y, x) swap and arc flattening - is right.  That is the check
worth running before every job.
"""

from __future__ import annotations

import dataclasses

from . import graphtec
from .geometry import Grid, Rect

# A decoded card must measure its nominal size to within this.  One
# Silhouette unit is 0.05 mm, and arc flattening is held to half of that, so
# anything above ~0.1 mm is a real error rather than quantisation.
SIZE_TOLERANCE_MM = 0.12

# How far a decoded card corner may sit from where the grid put it.
POSITION_TOLERANCE_MM = 0.12


@dataclasses.dataclass
class Decoded:
    """What was found in a stream."""

    commands: list[str] = dataclasses.field(default_factory=list)
    force: int | None = None
    speed: int | None = None
    # Every value seen, not just the last. The Cameo 4 dialect sends the
    # pressure twice (upstream does it and does not know why), so checking
    # only the final FX would miss a stream whose first one was wrong - a
    # liveness probe caught exactly that.
    forces: list[int] = dataclasses.field(default_factory=list)
    speeds: list[int] = dataclasses.field(default_factory=list)
    depth: int | None = None
    holder: int | None = None
    blade_diameter_mm: float | None = None
    acceleration: list[int] = dataclasses.field(default_factory=list)
    preamble_present: bool = False
    mark_type: str | None = None
    scan_command: str | None = None
    scan_args_mm: tuple[float, ...] | None = None
    # Each path is a list of (x, y) in mark-relative mm, as sent.
    paths: list[list[tuple[float, float]]] = dataclasses.field(
        default_factory=list)

    @property
    def closed_paths(self) -> list[list[tuple[float, float]]]:
        """Paths whose last point returns to the first: whole cards."""
        out = []
        for p in self.paths:
            if len(p) > 2 and abs(p[0][0] - p[-1][0]) < 1e-6 \
                    and abs(p[0][1] - p[-1][1]) < 1e-6:
                out.append(p)
        return out

    def path_bboxes(self) -> list[Rect]:
        out = []
        for p in self.paths:
            xs = [x for x, _ in p]
            ys = [y for _, y in p]
            out.append(Rect(min(xs), min(ys), max(xs) - min(xs),
                            max(ys) - min(ys)))
        return out


def decode(stream: bytes) -> Decoded:
    """Pull a stream apart into the settings and the geometry it carries."""
    d = Decoded()
    # Strip the leading escape sequences; they carry no ETX.
    body = stream
    while body[:1] == graphtec.CMD_ESC:
        body = body[2:]

    current: list[tuple[float, float]] = []
    for raw in body.split(graphtec.CMD_ETX):
        if not raw:
            continue
        cmd = raw.decode("latin-1")
        d.commands.append(cmd)

        if cmd.startswith("FX"):
            parts = cmd[2:].split(",")
            d.force = int(parts[0])
            d.forces.append(d.force)
            if len(parts) > 1:
                d.holder = int(parts[1])
        elif cmd.startswith("!"):
            parts = cmd[1:].split(",")
            d.speed = int(parts[0])
            d.speeds.append(d.speed)
            if len(parts) > 1:
                d.holder = int(parts[1])
        elif cmd.startswith("TF"):
            d.depth = int(cmd[2:].split(",")[0])
        elif cmd.startswith("TJ"):
            d.acceleration.append(int(cmd[2:]))
        elif cmd.startswith("FC"):
            parts = cmd[2:].split(",")
            # FC with one argument is the blade diameter; with three it is the
            # per-holder cutter offset, which we do not check.
            if len(parts) == 1:
                d.blade_diameter_mm = graphtec.su_to_mm(int(parts[0]))
        elif cmd == "TB99":
            d.preamble_present = True
        elif cmd.startswith("TB52,"):
            d.mark_type = {"0": "original/SD",
                           "2": "Cameo/Portrait"}.get(cmd[5:], cmd[5:])
        elif cmd.startswith(("TB123,", "TB124,", "TB23,")):
            name, _, args = cmd.partition(",")
            d.scan_command = name
            d.scan_args_mm = tuple(
                graphtec.su_to_mm(int(a)) for a in args.split(","))
        elif cmd.startswith("M"):
            if len(current) > 1:
                d.paths.append(current)
            y, x = cmd[1:].split(",")
            current = [(graphtec.su_to_mm(int(x)), graphtec.su_to_mm(int(y)))]
        elif cmd.startswith("D"):
            y, x = cmd[1:].split(",")
            current.append((graphtec.su_to_mm(int(x)),
                            graphtec.su_to_mm(int(y))))
    if len(current) > 1:
        d.paths.append(current)
    return d


# --------------------------------------------------------------------------
# Proving the verifier is alive
# --------------------------------------------------------------------------

def _split(stream: bytes) -> tuple[bytes, list[bytes]]:
    """The leading escape bytes, and the ETX-terminated commands after them."""
    head = b""
    body = stream
    while body[:1] == graphtec.CMD_ESC:
        head += body[:2]
        body = body[2:]
    return head, [c for c in body.split(graphtec.CMD_ETX) if c]


def _rejoin(head: bytes, commands: list[bytes]) -> bytes:
    return head + b"".join(c + graphtec.CMD_ETX for c in commands)


def _corrupt_force(stream: bytes) -> bytes | None:
    head, cmds = _split(stream)
    for i, c in enumerate(cmds):
        if c.startswith(b"FX"):
            force, _, rest = c[2:].partition(b",")
            new = max(1, (int(force) % 33) + 1)
            cmds[i] = b"FX%d,%s" % (new, rest)
            return _rejoin(head, cmds)
    return None


def _corrupt_position(stream: bytes, last: bool = False) -> bytes | None:
    """Move one whole card 1 mm, keeping its shape.

    The obvious version of this probe - shift the first `M` and nothing else
    - does not test what it claims to. Moving only the start point breaks
    the loop, so the stream is rejected by the *closed loops* check and the
    position check is never consulted. It looks caught while the check it
    was written for stays unproven, which is exactly the shared-decoder
    blindness a sibling session warned about.

    So this shifts every coordinate of the first path: same size, same
    closure, 1 mm from where it belongs, which is 8x the position tolerance
    and nothing else's business.
    """
    head, cmds = _split(stream)
    starts = [i for i, c in enumerate(cmds) if c.startswith(b"M")
              and i + 1 < len(cmds) and cmds[i + 1].startswith(b"D")]
    if not starts:
        return None
    start = starts[-1] if last else starts[0]
    shift = graphtec.su(1.0)
    i = start
    moved = False
    while i < len(cmds):
        c = cmds[i]
        if i > start and c.startswith(b"M"):
            break                      # the next path begins
        if c.startswith((b"M", b"D")):
            y, _, x = c[1:].partition(b",")
            cmds[i] = b"%s%d,%s" % (c[:1], int(y) + shift, x)
            moved = True
        i += 1
    return _rejoin(head, cmds) if moved else None


def _corrupt_speed(stream: bytes) -> bytes | None:
    head, cmds = _split(stream)
    for i, c in enumerate(cmds):
        if c.startswith(b"!"):
            speed, _, rest = c[1:].partition(b",")
            new = max(1, (int(speed) % 10) + 1)
            cmds[i] = b"!%d,%s" % (new, rest) if rest else b"!%d" % new
            return _rejoin(head, cmds)
    return None


def _path_slice(cmds: list[bytes]) -> tuple[int, int] | None:
    """The index range of the first path's M/D commands."""
    start = next((i for i, c in enumerate(cmds) if c.startswith(b"M")), None)
    if start is None:
        return None
    end = start + 1
    while end < len(cmds) and cmds[end].startswith(b"D"):
        end += 1
    return start, end


def _corrupt_size(stream: bytes) -> bytes | None:
    """Stretch one card by 1 mm without moving its origin.

    Only the points on the far edge move, so the loop still closes and its
    top-left corner is where it belongs: the size check has to be the thing
    that notices, not the position or closure checks.
    """
    head, cmds = _split(stream)
    span = _path_slice(cmds)
    if span is None:
        return None
    start, end = span
    ys = [int(cmds[i][1:].partition(b",")[0]) for i in range(start, end)]
    if not ys:
        return None
    far = max(ys)
    shift = graphtec.su(1.0)
    for i in range(start, end):
        c = cmds[i]
        y, _, x = c[1:].partition(b",")
        if int(y) == far:
            cmds[i] = b"%s%d,%s" % (c[:1], far + shift, x)
    return _rejoin(head, cmds)


def _corrupt_outside(stream: bytes) -> bytes | None:
    """Push one card right off the registered area.

    This trips the position check too, which is fine and expected - the
    point is that the out-of-bounds check must be among the ones that
    object, because it is the one guarding the blade against the mat.
    """
    head, cmds = _split(stream)
    span = _path_slice(cmds)
    if span is None:
        return None
    start, end = span
    shift = graphtec.su(500.0)
    for i in range(start, end):
        c = cmds[i]
        y, _, x = c[1:].partition(b",")
        cmds[i] = b"%s%d,%s" % (c[:1], int(y) + shift, x)
    return _rejoin(head, cmds)


def _drop_a_path(stream: bytes) -> bytes | None:
    """Delete one whole card's cut. A sheet with an uncut card in it."""
    head, cmds = _split(stream)
    span = _path_slice(cmds)
    if span is None:
        return None
    start, end = span
    del cmds[start:end]
    return _rejoin(head, cmds)


def _drop_preamble(stream: bytes) -> bytes | None:
    head, cmds = _split(stream)
    out = [c for c in cmds if c != b"TB99"]
    return _rejoin(head, out) if len(out) != len(cmds) else None


def _drop_mat_setup(stream: bytes) -> bytes | None:
    head, cmds = _split(stream)
    out = [cmd for cmd in cmds if cmd not in (b"TG1", b"TG2")]
    return _rejoin(head, out) if len(out) != len(cmds) else None


def _corrupt_mark_arm(stream: bytes) -> bytes | None:
    head, cmds = _split(stream)
    for i, cmd in enumerate(cmds):
        if cmd.startswith(b"TB51,"):
            cmds[i] = b"TB51,%d" % (int(cmd[5:]) + graphtec.su(1.0))
            return _rejoin(head, cmds)
    return None


def _corrupt_scan_window(stream: bytes) -> bytes | None:
    head, cmds = _split(stream)
    for i, c in enumerate(cmds):
        if c.startswith((b"TB123,", b"TB124,")):
            name, _, args = c.partition(b",")
            parts = args.split(b",")
            parts[0] = b"%d" % (int(parts[0]) + graphtec.su(5.0))
            cmds[i] = name + b"," + b",".join(parts)
            return _rejoin(head, cmds)
    return None


# Each probe breaks the stream in one specific way that a named check is
# supposed to catch. The label is the check that must reject it.
# Each probe breaks the stream in one way and names the check that must
# object. A probe may trip others too - some checks are genuinely entangled,
# and a cut pushed off the sheet is also in the wrong place - but the named
# one has to be among them, or it is still unproven.
def _corrupt_last_position(stream: bytes) -> bytes | None:
    return _corrupt_position(stream, last=True)


PROBES = (
    ("card position error", _corrupt_last_position),
    ("blade force", _corrupt_force),
    ("speed", _corrupt_speed),
    ("card position error", _corrupt_position),
    ("card size error", _corrupt_size),
    ("cuts inside the marks", _corrupt_outside),
    ("cut paths", _drop_a_path),
    ("registration preamble", _drop_preamble),
    ("registration mark dimensions", _corrupt_mark_arm),
    ("registration mat setup", _drop_mat_setup),
    ("scan window length", _corrupt_scan_window),
)


def _corrupt_imported_vertex(stream: bytes) -> bytes | None:
    """Change a vertex in the final pass of an imported design."""
    head, cmds = _split(stream)
    for index in range(len(cmds) - 1, -1, -1):
        command = cmds[index]
        if command.startswith(b"D"):
            y, _, x = command[1:].partition(b",")
            cmds[index] = b"D%d,%s" % (int(y) + graphtec.su(1), x)
            return _rejoin(head, cmds)
    return None


IMPORTED_PROBES = (
    ("imported path geometry", _corrupt_imported_vertex),
    ("cuts inside the sheet", _corrupt_outside),
)


def applicable_probes(rows):
    labels = {row.label for row in rows}
    return [(label, corrupt) for label, corrupt in PROBES + IMPORTED_PROBES
            if label in labels]


def liveness_failures(stream: bytes, *args, **kwargs) -> list[str]:
    """Break the stream on purpose and confirm the checks reject it.

    This is the difference between "the checks passed" and "the checks ran
    and passed". A verifier that always returns ok prints exactly the same
    all-clear table as a working one, and on this path that table is the
    only thing standing between a wrong stream and a blade moving across
    expensive card.

    So before a job is allowed through, the real stream is corrupted four
    ways - each one a fault a named check exists to catch - and each
    corruption is fed back through the same `expectations_met`. Anything
    that comes back clean means that check is not doing its job, and the
    caller must refuse rather than report a pass.

    It has to happen in the same run as the verification it certifies. A
    separate proving run produces output indistinguishable from a blind
    check's, so it is not evidence about this job.

    Two controls bracket the probes, for the same reason a sibling session
    put a no-op before and after its own: a probe's pass has to be
    attributable to the probe.

    The leading control runs the **unmodified** stream. A check that is
    already failing there gives its probe a free "hit" - the probe proves
    nothing, because the check was red before it was touched. That is the
    exact mirror of a probe satisfied by another probe's event, and it is
    not hypothetical: it was reachable here until this control existed.

    The trailing control re-runs the unmodified stream afterwards and
    requires the same answer, so no probe can have left anything behind.

    Returns the probes and controls that did NOT hold; empty means the
    checks are live and the result is attributable.
    """
    missed: list[str] = []

    # Leading control.
    try:
        base_ok, base_rows = expectations_met(stream, *args, **kwargs)
    except Exception as exc:                     # noqa: BLE001
        return [f"control: verifying the unmodified stream raised ({exc}), "
                f"so nothing in this run is attributable"]
    already_failing = {r.label for r in base_rows if not r.ok}
    # Which checks this run actually performs. An unregistered job has no
    # scan window and no in-bounds check, so a probe aimed at one of those
    # has nothing to prove and is absent rather than missed.
    for label, corrupt in applicable_probes(base_rows):
        if label in already_failing:
            missed.append(
                f"{label}: this check was already failing on the "
                f"unmodified stream, so breaking it on purpose proves "
                f"nothing about whether it works")
            continue
        broken = corrupt(stream)
        if broken is None:
            # Nothing of that kind in this stream - an unregistered job has
            # no preamble, for instance. Not a failure.
            continue
        if broken == stream:
            missed.append(f"{label}: the probe did not change the stream")
            continue
        try:
            ok, rows = expectations_met(broken, *args, **kwargs)
        except Exception as exc:          # noqa: BLE001
            missed.append(f"{label}: the check raised instead of failing "
                          f"({exc})")
            continue
        if ok:
            missed.append(f"{label}: a stream corrupted to break this check "
                          f"still passed every check")
            continue
        # Rejected - but by the right check? A probe caught by some *other*
        # check looks live while the one it was written for stays unproven.
        # Two checks reading the same decoded stream can hide each other
        # that way, so the target has to be named, not merely the verdict.
        failed = {r.label for r in rows if not r.ok}
        if label not in failed:
            missed.append(
                f"{label}: the probe was rejected, but by "
                f"{', '.join(sorted(failed)) or 'nothing'} rather than by "
                f"the check it targets, which is therefore still unproven")

    # Trailing control: the probes must not have changed the answer.
    try:
        after_ok, after_rows = expectations_met(stream, *args, **kwargs)
    except Exception as exc:                     # noqa: BLE001
        missed.append(f"control: re-verifying the unmodified stream after "
                      f"the probes raised ({exc})")
        return missed
    if after_ok != base_ok or {r.label for r in after_rows if not r.ok} \
            != already_failing:
        missed.append(
            "control: the unmodified stream verified differently before and "
            "after the probes, so a probe left state behind and no result "
            "in this run is attributable")
    return missed


@dataclasses.dataclass
class Row:
    label: str
    want: str
    got: str
    ok: bool


def expectations_met(stream: bytes, grid: Grid, mk, settings, device,
                     mode: str = "rounded", cards=None,
                     offset_mm: tuple[float, float] = (0.0, 0.0),
                     registration: bool = True, expected_paths=None
                     ) -> tuple[bool, list[Row]]:
    """Check a stream against the grid, marks and blade settings.

    Returns `(ok, rows)`; `rows` is meant to be printed as a table, pass or
    fail, so that a refused job shows *what* disagreed.
    """
    d = decode(stream)
    s = settings.clamped(device)
    # `cards` is the cards actually being cut, which is the grid minus any
    # skipped slots. Counting against the full grid would fail every job that
    # deliberately leaves one out.
    cards = grid.cards if cards is None else tuple(cards)
    rows: list[Row] = []

    def row(label, want, got, ok):
        rows.append(Row(label, str(want), str(got), bool(ok)))

    # Every force the stream carries must be the one asked for, not just
    # the last. A single-settings job that changes force part-way through is
    # a corrupted stream, and the machine would act on it.
    row("blade force", s.force,
        ", ".join(str(f) for f in d.forces) or "missing",
        bool(d.forces) and set(d.forces) == {s.force})
    row("speed", s.speed,
        ", ".join(str(v) for v in d.speeds) or "missing",
        bool(d.speeds) and set(d.speeds) == {s.speed})
    row("tool holder", s.holder, d.holder, d.holder == s.holder)
    if s.depth is not None and s.holder == 1:
        row("autoblade depth", s.depth, d.depth, d.depth == s.depth)

    # Registration. An unregistered job has none of this by design - it
    # cuts in sheet coordinates on a blank sheet - so asserting it anyway
    # made every `--no-registration` job unsendable.
    if not registration:
        row("registration", "not used", "not used", True)
        row("no scan commands sent", "none",
            d.scan_command or "none", d.scan_command is None)
    else:
        row("registration preamble", "present",
            "present" if d.preamble_present else "missing",
            d.preamble_present)
        row("mark type", "Cameo/Portrait", d.mark_type or "missing",
            d.mark_type == "Cameo/Portrait")

    if mk is not None and registration:
        scan_at = next((i for i, command in enumerate(d.commands)
                        if command.startswith(("TB123,", "TB124,", "TB23,"))), 0)
        before_scan = d.commands[:scan_at]
        mat = graphtec.registration_mat_commands(device, mk.sheet_w_mm, mk.sheet_h_mm)
        mat_commands = before_scan
        if d.scan_command == "TB123" and graphtec.captured_letter_scan(
                device, mk.reg_length_mm, mk.reg_width_mm, mk.origin_mm,
                (mk.length_mm, mk.thickness_mm)):
            mat = ("TG1", "FN0", "TB50,0", "TB99", "TB52,2", f"TB51,{graphtec.su(mk.length_mm)}", "TB53,20",
                   "TB55,1", "TB123,3918,5188,118,118", "TB99")
            mat_commands = d.commands
        if mat:
            row("registration mat setup", " ".join(mat),
                " ".join(mat_commands[:len(mat)]) or "missing",
                tuple(mat_commands[:len(mat)]) == mat)
        shape = graphtec.registration_preamble(device, (mk.length_mm, mk.thickness_mm))[3:5]
        sent_shape = tuple(command for command in before_scan if command.startswith(("TB51,", "TB53,")))
        row("registration mark dimensions", " ".join(shape),
            " ".join(sent_shape) or "missing", sent_shape == shape)
        want_cmd = "TB124" if (mk.pattern == "four" and device.quad_regmarks) \
            else "TB123"
        row("scan command", want_cmd, d.scan_command or "missing",
            d.scan_command == want_cmd)
        if d.scan_args_mm and len(d.scan_args_mm) >= 2:
            got_len, got_wid = d.scan_args_mm[0], d.scan_args_mm[1]
            ok_len = abs(got_len - mk.reg_length_mm) <= 0.05
            ok_wid = abs(got_wid - mk.reg_width_mm) <= 0.05
            row("scan window length", f"{mk.reg_length_mm:.2f} mm",
                f"{got_len:.2f} mm", ok_len)
            row("scan window width", f"{mk.reg_width_mm:.2f} mm",
                f"{got_wid:.2f} mm", ok_wid)
        else:
            row("scan window", "length and width", "missing", False)

    # Geometry: the check that actually protects the stock.
    passes = max(1, s.passes)
    if expected_paths is not None:
        ox, oy = mk.origin_mm if mk is not None and registration else (0.0, 0.0)
        ox, oy = ox - offset_mm[0], oy - offset_mm[1]
        expected = []
        for path in expected_paths:
            points = [(graphtec.su(x - ox), graphtec.su(y - oy))
                      for x, y in path.points]
            if path.closed:
                points.append(points[0])
            expected.append(points)
        got = [[(graphtec.su(x), graphtec.su(y)) for x, y in path] for path in d.paths]
        wanted = expected * passes
        row("cut paths", len(wanted), len(got), len(wanted) == len(got))
        matches = wanted == got
        row("imported path geometry", "all vertices in order on every pass",
            "matches" if matches else "path coordinates differ", matches)
        if not registration:
            inside = all(-0.05 <= x <= grid.sheet_w_mm + 0.05
                         and -0.05 <= y <= grid.sheet_h_mm + 0.05
                         for path in d.paths for x, y in path)
            row("cuts inside the sheet", "inside", "inside" if inside else "outside", inside)
    elif mode == "cross":
        want_paths = (2 * grid.cols + 2 * grid.rows) * passes
        row("cut paths", want_paths, len(d.paths),
            len(d.paths) == want_paths)
    else:
        want_paths = len(cards) * passes
        row("cut paths", want_paths, len(d.paths),
            len(d.paths) == want_paths)
        row("closed loops", want_paths, len(d.closed_paths),
            len(d.closed_paths) == want_paths)

        # Without registration the machine's origin is the sheet corner,
        # so no mark shift is undone.
        ox, oy = (mk.origin_mm if (mk is not None and registration)
                  else (0.0, 0.0))
        # Undo the calibration offset before measuring, or a correctly
        # calibrated job reads as a position error exactly its own size.
        ox -= offset_mm[0]
        oy -= offset_mm[1]
        worst_size = 0.0
        worst_pos = 0.0
        boxes = d.path_bboxes()
        for index, box in enumerate(boxes):
            if not cards:
                break
            rect = cards[index % len(cards)]
            worst_size = max(worst_size,
                             abs(box.w - rect.w), abs(box.h - rect.h))
            worst_pos = max(worst_pos,
                            abs((box.x + ox) - rect.x),
                            abs((box.y + oy) - rect.y))
        row("card size error", f"<= {SIZE_TOLERANCE_MM} mm",
            f"{worst_size:.3f} mm", worst_size <= SIZE_TOLERANCE_MM)
        row("card position error", f"<= {POSITION_TOLERANCE_MM} mm",
            f"{worst_pos:.3f} mm", worst_pos <= POSITION_TOLERANCE_MM)

    # Nothing may be cut outside the registered area, or the blade leaves the
    # sheet and scores the mat.
    if mk is not None and registration and d.paths:
        xs = [x for p in d.paths for x, _ in p]
        ys = [y for p in d.paths for _, y in p]
        inside = (min(xs) >= -0.05 and min(ys) >= -0.05
                  and max(xs) <= mk.reg_width_mm + 0.05
                  and max(ys) <= mk.reg_length_mm + 0.05)
        row("cuts inside the marks",
            f"0..{mk.reg_width_mm:.1f} x 0..{mk.reg_length_mm:.1f} mm",
            f"{min(xs):.2f}..{max(xs):.2f} x {min(ys):.2f}..{max(ys):.2f} mm",
            inside)

    # The sheet has to fit the machine.
    row("sheet fits the cutter", f"<= {device.width_mm:g} mm wide",
        f"{grid.sheet_w_mm:g} mm", grid.sheet_w_mm <= device.width_mm)

    return all(r.ok for r in rows), rows
