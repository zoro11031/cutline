"""Cut paths, and the SVG / DXF files that carry them.

A cut template is only useful if it is *coordinate locked*: 1 mm in this file
has to be 1 mm on the mat, with no import-time scaling anywhere in between.
That is handled differently per format:

SVG
    `width`/`height` are given in explicit `mm` and the `viewBox` is the same
    numbers unitless, so one user unit is exactly one millimetre.  Silhouette
    Studio honours that; Inkscape honours that.  What neither honours is an
    SVG with a pixel `width` and a `viewBox` that disagrees, which is the
    usual cause of a template that cuts at 96/72 or 25.4/96 of the right size.

DXF
    DXF carries no units of its own, so `$INSUNITS` and `$MEASUREMENT` are
    written into the header and every coordinate is plain millimetres.

Rounded corners are emitted as **closed POLYLINE entities with bulge-encoded
arcs** rather than as LINE + ARC pairs.  That is not a stylistic choice: SCM's
`dxf_manager.py` records that Silhouette Studio renders a visible sharp corner
where a LINE meets an ARC, and that bulges are what fixed it.  The bulge for a
90 degree arc is tan(22.5 degrees).

The USB path does not use either file.  It takes the same `CutPath` list, so
what gets streamed and what gets written are generated once, from one source.
"""

from __future__ import annotations

import dataclasses
import math

from .geometry import Grid, Rect
from .regmarks import Marks

# A quarter-circle bulge factor: tan(45/2 degrees).  From SCM dxf_manager.py.
BULGE_90 = math.tan(math.radians(22.5))

# How finely an arc is broken into straight segments when a polyline is all
# the consumer can take (the USB stream, and SVG proofs).  One Silhouette unit
# is 0.05 mm, so flattening to half of that puts the error below the smallest
# step the machine can take, and no tighter - extra points are just stream.
CHORD_TOLERANCE_MM = 0.025

ROUNDED = "rounded"
SQUARE = "square"
CROSS = "cross"
MODES = (ROUNDED, SQUARE, CROSS)


@dataclasses.dataclass(frozen=True)
class CutPath:
    """One continuous path the blade follows, in sheet mm, top-left origin."""

    points: tuple[tuple[float, float], ...]
    closed: bool
    label: str = ""

    def length_mm(self) -> float:
        pts = list(self.points) + ([self.points[0]] if self.closed else [])
        return sum(math.dist(a, b) for a, b in zip(pts, pts[1:]))


# --------------------------------------------------------------------------
# Arc flattening
# --------------------------------------------------------------------------

def _arc_segments(radius: float, sweep: float = math.pi / 2,
                  tolerance: float = CHORD_TOLERANCE_MM) -> int:
    """Segments needed so no chord deviates from the arc by > tolerance."""
    if radius <= 0:
        return 1
    ratio = max(-1.0, min(1.0, 1.0 - tolerance / radius))
    max_step = 2 * math.acos(ratio)
    if max_step <= 0:
        return 1
    return max(1, math.ceil(sweep / max_step))


def _quarter_arc(cx: float, cy: float, r: float, start: float,
                 tolerance: float) -> list[tuple[float, float]]:
    """A 90 degree arc centred (cx, cy), from angle `start`, clockwise on
    screen (y down).  Excludes the first point; the caller already has it."""
    n = _arc_segments(r, math.pi / 2, tolerance)
    out = []
    for i in range(1, n + 1):
        a = start + (math.pi / 2) * (i / n)
        out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


def rounded_rect_points(rect: Rect, radius: float,
                        tolerance: float = CHORD_TOLERANCE_MM
                        ) -> tuple[tuple[float, float], ...]:
    """A rounded rectangle as a flattened closed polyline, clockwise.

    Starts at the top edge just right of the top-left corner, which keeps the
    blade's entry point on a straight run rather than mid-arc.
    """
    r = max(0.0, min(radius, min(rect.w, rect.h) / 2))
    if r == 0:
        return ((rect.x, rect.y), (rect.x2, rect.y),
                (rect.x2, rect.y2), (rect.x, rect.y2))
    x, y, x2, y2 = rect.x, rect.y, rect.x2, rect.y2
    pts: list[tuple[float, float]] = [(x + r, y)]
    pts.append((x2 - r, y))
    # Angles are measured with y down, so a clockwise screen path runs from
    # -90 degrees (top) to 0 (right) around the top-right corner.
    pts += _quarter_arc(x2 - r, y + r, r, -math.pi / 2, tolerance)
    pts.append((x2, y2 - r))
    pts += _quarter_arc(x2 - r, y2 - r, r, 0.0, tolerance)
    pts.append((x + r, y2))
    pts += _quarter_arc(x + r, y2 - r, r, math.pi / 2, tolerance)
    pts.append((x, y + r))
    pts += _quarter_arc(x + r, y + r, r, math.pi, tolerance)
    # The last arc lands back on the start point; drop the duplicate.
    if math.dist(pts[0], pts[-1]) < 1e-9:
        pts.pop()
    return tuple(pts)


# --------------------------------------------------------------------------
# Cut paths for a grid
# --------------------------------------------------------------------------

def cut_paths(grid: Grid, mode: str = ROUNDED, radius_mm: float | None = None,
              tolerance: float = CHORD_TOLERANCE_MM,
              skip: "frozenset[int] | tuple[int, ...]" = ()) -> tuple[CutPath, ...]:
    """The paths that separate the cards on one sheet.

    `skip` leaves those card indices uncut, for the slot you deliberately
    left empty so a card would stop crowding a registration mark. It is the
    counterpart of Card Maker's `create_pdf.py --skip`, and the two have to
    agree: skipping a slot here that was printed cuts nothing out of it, and
    printing a slot that is skipped here leaves an uncut card on the sheet.

    `rounded` and `square` cut each card as its own closed loop, so a card is
    finished before the blade moves on and a mis-feed costs one card rather
    than the sheet.  `cross` cuts straight lines all the way across instead,
    for the workflow where the corners are rounded afterwards with a punch;
    it is far faster and much less demanding of the blade, but it cannot be
    used on a sheet whose cards do not share edges.
    """
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r}; known: {', '.join(MODES)}")
    radius = grid.card.radius_mm if radius_mm is None else radius_mm

    if mode == CROSS:
        return _cross_paths(grid)

    skipped = frozenset(skip)
    out = []
    for i, rect in enumerate(grid.cards):
        if i in skipped:
            continue
        row, col = divmod(i, grid.cols)
        pts = rounded_rect_points(rect, radius if mode == ROUNDED else 0.0,
                                  tolerance)
        out.append(CutPath(pts, closed=True,
                           label=f"card r{row + 1}c{col + 1}"))
    return tuple(out)


def _cross_paths(grid: Grid) -> tuple[CutPath, ...]:
    """Full-width and full-height cuts along every card edge.

    Only valid when the cards tile: if two columns do not share an x edge,
    a straight cut through one would run through the face of the other.
    """
    xs = sorted({round(v, 4) for c in grid.cards for v in (c.x, c.x2)})
    ys = sorted({round(v, 4) for c in grid.cards for v in (c.y, c.y2)})
    if len(xs) != 2 * grid.cols or len(ys) != 2 * grid.rows:
        # Shared edges collapsed, which is what we want; anything else means
        # the columns are not aligned and a cross cut is unsafe.
        if len(xs) > 2 * grid.cols or len(ys) > 2 * grid.rows:
            raise ValueError(
                "cross cuts need every column and row to share an edge; this "
                "grid has gaps, so a straight cut would cross a card face")
    bounds = grid.bounds
    out = []
    for x in xs:
        out.append(CutPath(((x, bounds.y), (x, bounds.y2)), False, f"x={x:g}"))
    for y in ys:
        out.append(CutPath(((bounds.x, y), (bounds.x2, y)), False, f"y={y:g}"))
    return tuple(out)


def total_length_mm(paths) -> float:
    return sum(p.length_mm() for p in paths)


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------

def _n(v: float) -> str:
    """Format a coordinate: enough precision for 0.05 mm steps, no float dust."""
    return f"{v:.4f}".rstrip("0").rstrip(".") or "0"


def _svg_rounded_path(rect: Rect, radius: float) -> str:
    """A rounded rectangle as true SVG arcs, clockwise."""
    r = max(0.0, min(radius, min(rect.w, rect.h) / 2))
    x, y, x2, y2 = rect.x, rect.y, rect.x2, rect.y2
    if r == 0:
        return (f"M {_n(x)} {_n(y)} H {_n(x2)} V {_n(y2)} H {_n(x)} Z")
    a = f"A {_n(r)} {_n(r)} 0 0 1"
    return (f"M {_n(x + r)} {_n(y)} "
            f"H {_n(x2 - r)} {a} {_n(x2)} {_n(y + r)} "
            f"V {_n(y2 - r)} {a} {_n(x2 - r)} {_n(y2)} "
            f"H {_n(x + r)} {a} {_n(x)} {_n(y2 - r)} "
            f"V {_n(y + r)} {a} {_n(x + r)} {_n(y)} Z")


def write_svg(path, grid: Grid, paths, mode: str = ROUNDED,
              radius_mm: float | None = None, mk: Marks | None = None,
              proof: bool = False, note: str = "") -> str:
    """Write the cut template as SVG.

    With `proof=True` the registration marks and a page outline are drawn as
    well, for holding against the printed sheet to check alignment by eye.
    **A proof must never be sent to the cutter**: Studio would try to cut the
    marks.  The cut file, `proof=False`, contains cut geometry only.
    """
    radius = grid.card.radius_mm if radius_mm is None else radius_mm
    W, H = grid.sheet_w_mm, grid.sheet_h_mm
    out: list[str] = []
    out.append('<?xml version="1.0" encoding="UTF-8"?>')
    out.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'width="{_n(W)}mm" height="{_n(H)}mm" '
        f'viewBox="0 0 {_n(W)} {_n(H)}">')
    # One user unit == one millimetre.  Anything that rewrites this header is
    # rescaling the template.
    out.append(f'  <title>cut-proxies {grid.describe()}</title>')
    desc = [grid.describe(), f"mode {mode}", f"radius {radius:g} mm",
            f"1 user unit = 1 mm"]
    if mk is not None:
        desc.append(mk.describe())
    if note:
        desc.append(note)
    out.append('  <desc>' + " | ".join(
        d.replace("&", "&amp;").replace("<", "&lt;") for d in desc) + '</desc>')

    if proof:
        out.append(f'  <g id="page" fill="none" stroke="#bbbbbb" '
                   f'stroke-width="0.2">')
        out.append(f'    <rect x="0" y="0" width="{_n(W)}" height="{_n(H)}"/>')
        out.append('  </g>')
        if mk is not None:
            out.append('  <g id="registration-marks-DO-NOT-CUT" fill="#000000">')
            if mk.square is not None:
                s = mk.square
                out.append(f'    <rect x="{_n(s.x)}" y="{_n(s.y)}" '
                           f'width="{_n(s.w)}" height="{_n(s.h)}"/>')
            out.append(f'    <g stroke="#000000" stroke-width="{_n(mk.thickness_mm)}" '
                       f'stroke-linecap="butt">')
            for ell in mk.ells:
                for x1, y1, x2, y2 in ell.arms():
                    out.append(f'      <line x1="{_n(x1)}" y1="{_n(y1)}" '
                               f'x2="{_n(x2)}" y2="{_n(y2)}"/>')
            out.append('    </g>')
            out.append('  </g>')

    out.append('  <g id="cut" fill="none" stroke="#ff0000" stroke-width="0.1">')
    if mode == CROSS:
        for p in paths:
            (x1, y1), (x2, y2) = p.points[0], p.points[-1]
            out.append(f'    <line x1="{_n(x1)}" y1="{_n(y1)}" '
                       f'x2="{_n(x2)}" y2="{_n(y2)}"/>')
    else:
        # True arcs, not the flattened polyline, so the file stays small and
        # editable.  The flattened form is what the USB stream uses; both come
        # from the same rectangles and the same radius.
        for rect in grid.cards:
            out.append(f'    <path d="'
                       f'{_svg_rounded_path(rect, radius if mode == ROUNDED else 0.0)}"/>')
    out.append('  </g>')
    out.append('</svg>')
    text = "\n".join(out) + "\n"
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text


# --------------------------------------------------------------------------
# DXF
# --------------------------------------------------------------------------

def _dxf_pair(code: int, value) -> str:
    return f"{code:>3}\n{value}\n"


def write_dxf(path, grid: Grid, mode: str = ROUNDED,
              radius_mm: float | None = None) -> str:
    """Write the cut template as DXF in millimetres.

    Rounded cards become closed POLYLINE entities whose corner vertices carry
    a bulge, which is what Silhouette Studio joins smoothly.  Y is flipped,
    because DXF counts upward from a bottom-left origin while every other
    coordinate in this project counts down from the top-left.
    """
    radius = grid.card.radius_mm if radius_mm is None else radius_mm
    H = grid.sheet_h_mm

    def fy(y: float) -> float:
        return H - y

    body: list[str] = []

    if mode == CROSS:
        for p in _cross_paths(grid):
            (x1, y1), (x2, y2) = p.points[0], p.points[-1]
            body.append(_dxf_pair(0, "LINE") + _dxf_pair(8, "CUT")
                        + _dxf_pair(10, _n(x1)) + _dxf_pair(20, _n(fy(y1)))
                        + _dxf_pair(30, "0.0")
                        + _dxf_pair(11, _n(x2)) + _dxf_pair(21, _n(fy(y2)))
                        + _dxf_pair(31, "0.0"))
    else:
        r = radius if mode == ROUNDED else 0.0
        for rect in grid.cards:
            body.append(_dxf_polyline(rect, r, fy))

    out = []
    # HEADER: units must be stated or the importer guesses.
    out.append(_dxf_pair(0, "SECTION") + _dxf_pair(2, "HEADER"))
    out.append(_dxf_pair(9, "$ACADVER") + _dxf_pair(1, "AC1009"))
    out.append(_dxf_pair(9, "$INSUNITS") + _dxf_pair(70, 4))      # 4 = mm
    out.append(_dxf_pair(9, "$MEASUREMENT") + _dxf_pair(70, 1))   # 1 = metric
    out.append(_dxf_pair(9, "$EXTMIN") + _dxf_pair(10, "0.0")
               + _dxf_pair(20, "0.0") + _dxf_pair(30, "0.0"))
    out.append(_dxf_pair(9, "$EXTMAX") + _dxf_pair(10, _n(grid.sheet_w_mm))
               + _dxf_pair(20, _n(H)) + _dxf_pair(30, "0.0"))
    out.append(_dxf_pair(0, "ENDSEC"))
    out.append(_dxf_pair(0, "SECTION") + _dxf_pair(2, "ENTITIES"))
    out.extend(body)
    out.append(_dxf_pair(0, "ENDSEC"))
    out.append(_dxf_pair(0, "EOF"))
    text = "".join(out)
    if path is not None:
        with open(path, "w", encoding="ascii", newline="\n") as fh:
            fh.write(text)
    return text


def _dxf_polyline(rect: Rect, radius: float, fy) -> str:
    """One card as a closed POLYLINE with bulge-encoded 90 degree corners.

    Vertices run counter-clockwise in DXF space (which is clockwise on
    screen), and each arc-start vertex carries the bulge; a positive bulge is
    a counter-clockwise arc, so the sense has to match the vertex order or
    the corners bulge outward into little ears.
    """
    x, y2d, w, h = rect.x, rect.y, rect.w, rect.h
    r = max(0.0, min(radius, min(w, h) / 2))
    y = fy(y2d) - h          # bottom edge in DXF coordinates

    if r == 0:
        verts = [(x, y, 0.0), (x + w, y, 0.0), (x + w, y + h, 0.0),
                 (x, y + h, 0.0)]
    else:
        b = BULGE_90
        verts = [
            (x + w - r, y, b),          # bottom-right arc starts
            (x + w, y + r, 0.0),
            (x + w, y + h - r, b),      # top-right arc starts
            (x + w - r, y + h, 0.0),
            (x + r, y + h, b),          # top-left arc starts
            (x, y + h - r, 0.0),
            (x, y + r, b),              # bottom-left arc starts
            (x + r, y, 0.0),
        ]

    s = (_dxf_pair(0, "POLYLINE") + _dxf_pair(8, "CUT") + _dxf_pair(66, 1)
         + _dxf_pair(70, 1)             # 1 = closed
         + _dxf_pair(10, "0.0") + _dxf_pair(20, "0.0") + _dxf_pair(30, "0.0"))
    for vx, vy, bulge in verts:
        s += (_dxf_pair(0, "VERTEX") + _dxf_pair(8, "CUT")
              + _dxf_pair(10, _n(vx)) + _dxf_pair(20, _n(vy))
              + _dxf_pair(30, "0.0"))
        if bulge:
            s += _dxf_pair(42, f"{bulge:.6f}")
    s += _dxf_pair(0, "SEQEND") + _dxf_pair(8, "CUT")
    return s
