"""Silhouette optical registration marks: geometry, scan window, clearances.

The cutter does not read a template.  It reads three (or four) printed marks
with an optical sensor, decides where the sheet actually is, and then treats
the top-left mark as the origin of every coordinate it is subsequently given.
So two numbers have to be right or nothing else matters:

* where the marks are **printed**, which this module defines, and
* where the cutter is **told to look**, which `scan_window` derives from the
  same numbers.

Get the first wrong and the sensor never finds a mark.  Get the second wrong
and it finds them but the cut lands somewhere else.

Mark geometry is taken from Silhouette Card Maker's `page_manager.py`
(`generate_reg_mark`) and the constants in its SPECIFICATION.md.  Note that
the prose in that specification says the 5x5 mm square is *centred* on the
inset, while its own code places the square's **corner** there
(`Rectangle((inset, paper_height - inset - 5), 5, 5)` in matplotlib's
lower-left-origin coordinates).  The code wins: it is what the published
templates were cut against, and it is also what Proxy Press draws.
"""

from __future__ import annotations

import dataclasses

from .geometry import Grid, Rect

# --------------------------------------------------------------------------
# Constraints, from SCM page_manager.py
# --------------------------------------------------------------------------

MIN_LENGTH_MM = 5.0
MAX_LENGTH_MM = 20.0
MIN_THICKNESS_MM = 0.5
MAX_THICKNESS_MM = 1.0
MIN_INSET_MM = 10.0
MAX_INSET_MM = 86.36
PADDING_MM = 1.5            # extra clearance SCM keeps around a mark

# The clearance an auto-sized arm keeps from the nearest card: one card
# gap, which is what SCM's own max-length formula subtracts.
SCM_SAFETY_MM = 1.25

SQUARE_MM = 5.0             # the top-left square is always 5x5 mm

# SCM's two documented presets.
DEFAULT_INSET_MM = 10.0
BORDERLESS_INSET_MM = 3.5   # below MIN_INSET_MM on purpose; see note below
DEFAULT_THICKNESS_MM = 1.0

# Patterns.
THREE = "three"              # square top-left + L at two corners: every Cameo
FOUR = "four"                # L at all four corners: Cameo 5 Alpha only
PROXYPRESS = "proxypress"    # what Proxy Press actually prints; see FINDINGS
PATTERNS = (THREE, FOUR, PROXYPRESS)


@dataclasses.dataclass(frozen=True)
class Ell:
    """One L-shaped mark: a corner point and two arms drawn inward.

    `dx`/`dy` are +1 or -1 and say which way the arms run from the corner, so
    a renderer never has to know which corner of the sheet this is.
    """

    corner_x: float
    corner_y: float
    dx: int
    dy: int
    length: float
    thickness: float

    def arms(self) -> tuple[tuple[float, float, float, float], ...]:
        """The two arms as (x1, y1, x2, y2) centre-lines.

        SCM shortens each arm by half the line thickness so that the two arms
        meet flush at the corner instead of overshooting by a half-width.
        """
        reach = self.length - self.thickness / 2
        return (
            (self.corner_x, self.corner_y,
             self.corner_x + self.dx * reach, self.corner_y),
            (self.corner_x, self.corner_y,
             self.corner_x, self.corner_y + self.dy * reach),
        )

    def bbox(self) -> Rect:
        x = self.corner_x if self.dx > 0 else self.corner_x - self.length
        y = self.corner_y if self.dy > 0 else self.corner_y - self.length
        return Rect(x, y, self.length, self.length).grown(self.thickness / 2)


@dataclasses.dataclass(frozen=True)
class Marks:
    """Every mark on one sheet, plus the numbers the cutter needs."""

    pattern: str
    sheet_w_mm: float
    sheet_h_mm: float
    inset_mm: float
    length_mm: float
    thickness_mm: float
    square: Rect | None
    ells: tuple[Ell, ...]

    # ---- what the cutter is told -------------------------------------
    @property
    def origin_mm(self) -> tuple[float, float]:
        """The registration origin: the top-left mark's corner.

        After a successful scan the cutter places logical (0, 0) here, so
        every cut coordinate is measured from this point, not from the paper
        corner.
        """
        return (self.inset_mm, self.inset_mm)

    @property
    def reg_width_mm(self) -> float:
        """Distance from the top-left mark to the top-right mark, in x."""
        return self.sheet_w_mm - 2 * self.inset_mm

    @property
    def reg_length_mm(self) -> float:
        """Distance from the top-left mark to the bottom-left mark, in y."""
        return self.sheet_h_mm - 2 * self.inset_mm

    def bboxes(self) -> tuple[Rect, ...]:
        """The actual ink of each mark, for drawing it."""
        out = [e.bbox() for e in self.ells]
        if self.square is not None:
            out.append(self.square.grown(self.thickness_mm / 2))
        return tuple(out)

    def corner_zones(self) -> tuple[Rect, ...]:
        """The square regions nothing printed may enter.

        This is SCM's corner rule, not a bounding box of the ink: a
        `length` x `length` square anchored at each mark's inset corner.  It
        matters that it is exactly that and no larger, because the same rule
        is what chose the layout - `Grid.max_mark_length_mm` already carries a
        one-card-distance safety margin inside it.  Growing the zone here by a
        padding or by half the line width would make this check reject SCM's
        own published layouts, which are known good.

        The top-left square mark is 5 mm on a side whatever the arm length is,
        so its zone is the larger of the two.
        """
        side = self.length_mm
        zones = []
        for e in self.ells:
            x = e.corner_x if e.dx > 0 else e.corner_x - side
            y = e.corner_y if e.dy > 0 else e.corner_y - side
            zones.append(Rect(x, y, side, side))
        if self.square is not None:
            s = max(side, SQUARE_MM)
            zones.append(Rect(self.square.x, self.square.y, s, s))
        return tuple(zones)

    def describe(self) -> str:
        n = len(self.ells) + (1 if self.square else 0)
        return (f"{self.pattern}: {n} marks, inset {self.inset_mm:g} mm, "
                f"arm {self.length_mm:g} mm, line {self.thickness_mm:g} mm; "
                f"scan window {self.reg_width_mm:.2f} x "
                f"{self.reg_length_mm:.2f} mm from "
                f"({self.inset_mm:g}, {self.inset_mm:g})")


def clamp(inset_mm: float, length_mm: float,
          thickness_mm: float) -> tuple[float, float, float]:
    """Apply SCM's own clamps, which are the machine's limits.

    The one deliberate exception is the inset: SCM declares MIN_REG_INSET_MM
    = 10 mm but ships a `borderless` preset at 3.5 mm and uses it for the 3x3
    layouts, so a smaller inset is allowed here rather than silently raised,
    which would move every mark and invalidate the template.
    """
    length = max(MIN_LENGTH_MM, min(length_mm, MAX_LENGTH_MM))
    thickness = max(MIN_THICKNESS_MM, min(thickness_mm, MAX_THICKNESS_MM))
    inset = min(inset_mm, MAX_INSET_MM)
    return inset, length, thickness


def marks(sheet_w_mm: float, sheet_h_mm: float,
          inset_mm: float = DEFAULT_INSET_MM,
          length_mm: float = MIN_LENGTH_MM,
          thickness_mm: float = DEFAULT_THICKNESS_MM,
          pattern: str = THREE) -> Marks:
    """Build the marks for one sheet.

    Corner assignment follows SCM exactly:

    * THREE - a filled 5x5 square at the top-left, L marks at the bottom-left
      and the top-right, and **nothing at the bottom-right**.
    * FOUR - L marks at all four corners and no square (Cameo 5 Alpha).
    * PROXYPRESS - the square plus L marks at all three remaining corners,
      which is what Proxy Press prints today.
    """
    if pattern not in PATTERNS:
        raise ValueError(
            f"unknown pattern {pattern!r}; known: {', '.join(PATTERNS)}")
    inset, length, thickness = clamp(inset_mm, length_mm, thickness_mm)
    W, H = sheet_w_mm, sheet_h_mm

    tl = Ell(inset, inset, +1, +1, length, thickness)
    tr = Ell(W - inset, inset, -1, +1, length, thickness)
    bl = Ell(inset, H - inset, +1, -1, length, thickness)
    br = Ell(W - inset, H - inset, -1, -1, length, thickness)

    square = Rect(inset, inset, SQUARE_MM, SQUARE_MM)

    if pattern == FOUR:
        return Marks(pattern, W, H, inset, length, thickness,
                     None, (tl, tr, bl, br))
    if pattern == PROXYPRESS:
        return Marks(pattern, W, H, inset, length, thickness,
                     square, (tr, bl, br))
    return Marks(pattern, W, H, inset, length, thickness, square, (tr, bl))


def for_grid(grid: Grid, inset_mm: float = DEFAULT_INSET_MM,
             length_mm: float | None = None,
             thickness_mm: float = DEFAULT_THICKNESS_MM,
             pattern: str = THREE,
             bleed_mm: float | None = None) -> Marks:
    """Marks sized for a grid, defaulting the arm to the longest that clears.

    `Grid.max_mark_length_mm` is a per-engine estimate and only the Card
    Maker engine computes it the way the corner rule works. The Proxy Press
    engine reported the slack to the nearer edge, which on an eight-up
    landscape sheet is an arm long enough to sit *on top of* four cards -
    the marks and the grid both want the same corners, so an estimate that
    does not model the rule gets it wrong in the unsafe direction.

    So when no length is asked for, find the longest arm that actually
    clears, by asking. Searching beats estimating here: it is a handful of
    rectangle tests, it is correct for any grid from any engine, and the
    answer is checkable against the thing it has to satisfy.

    The search keeps a safety margin of one card gap, which is what Card
    Maker's own formula subtracts. Without it the answer is the longest arm
    that does not *quite* touch a card - 0.05 mm of clearance on an
    eight-up sheet - and an arm that long makes the sensor's job harder for
    no benefit. Longest-that-does-not-collide is not the same as
    longest-that-is-sensible.
    """
    if bleed_mm is None:
        bleed_mm = grid.card_distance_mm or SCM_SAFETY_MM
    if length_mm is not None:
        return marks(grid.sheet_w_mm, grid.sheet_h_mm, inset_mm, length_mm,
                     thickness_mm, pattern)

    best = None
    # Quarter-millimetre steps: finer than the sensor cares about, and the
    # whole search is still a few dozen cheap tests.
    step = 0.25
    candidate = MIN_LENGTH_MM
    while candidate <= MAX_LENGTH_MM + 1e-9:
        mk = marks(grid.sheet_w_mm, grid.sheet_h_mm, inset_mm, candidate,
                   thickness_mm, pattern)
        if collisions(grid, mk, bleed_mm=bleed_mm):
            break
        best = mk
        candidate += step
    if best is None:
        # Nothing clears, not even the shortest arm the machine accepts.
        # Return it anyway: the caller's collision check will refuse the
        # job and say which cards are in the way, which is more use than
        # an exception from in here.
        return marks(grid.sheet_w_mm, grid.sheet_h_mm, inset_mm,
                     MIN_LENGTH_MM, thickness_mm, pattern)
    return best


# --------------------------------------------------------------------------
# Clearance
# --------------------------------------------------------------------------

def collisions(grid: Grid, mk: Marks, bleed_mm: float = 0.0,
               padding_mm: float = 0.0) -> list[str]:
    """Every card that intrudes into a mark's corner exclusion zone.

    A mark the sensor cannot read is the single most common cause of a ruined
    sheet, and card art printed over a mark is unreadable.  The caller is
    expected to refuse the job on a non-empty result, not warn about it.

    `padding_mm` defaults to zero because `corner_zones` is already the
    clearance rule the layout was chosen under; raise it only to ask a
    deliberately stricter question.
    """
    out: list[str] = []
    zones = [z.grown(padding_mm) if padding_mm else z
             for z in mk.corner_zones()]
    for i, c in enumerate(grid.cards):
        area = c.grown(bleed_mm)
        for z in zones:
            if area.overlaps(z):
                row, col = divmod(i, grid.cols)
                out.append(
                    f"card {i + 1} (row {row + 1}, column {col + 1}) at "
                    f"({c.x:.2f}, {c.y:.2f}) mm overlaps a registration-mark "
                    f"exclusion zone at ({z.x:.2f}, {z.y:.2f}) "
                    f"{z.w:.2f}x{z.h:.2f} mm")
                break
    return out


# How much clear paper a mark wants around it before the optical sensor can
# start reading a card edge as part of the mark.
#
# No number for this is published. What is documented is the failure: the
# Silhouette Card Maker cutting guide tells you to cover the bottom-left card
# with a sticky note during registration, "because this card sits close to the
# bottom-left L registration mark, and the machine can confuse the two", and
# `create_pdf.py --skip N` exists to leave that slot empty instead. Measuring
# its own layouts, the offending card clears the mark's corner zone by about
# 1.3 mm. So the threshold below is a judgement call, set well above the
# distance that is known to fail and below anything that would flag a layout
# with real room. Treat a hint as "mask this card", never as "this is broken".
MASK_CLEARANCE_MM = 5.0


def mask_hints(grid: Grid, mk: Marks,
               clearance_mm: float = MASK_CLEARANCE_MM) -> list[dict]:
    """Cards close enough to a mark that the sensor may read the card instead.

    Returns one entry per offending card, nearest first, each with the card
    index, its row and column, which mark it crowds and by how much. The
    caller should tell the operator to cover those cards for the scan and
    uncover them before the cut — not refuse the job, because masking is a
    perfectly good answer and the layout may be the one we want.
    """
    zones = mk.corner_zones()
    corners = [(z, _zone_name(z, mk)) for z in zones]
    out: list[dict] = []
    for i, c in enumerate(grid.cards):
        best = None
        for z, name in corners:
            # Separation between two rectangles: zero on an axis they overlap.
            dx = max(z.x - c.x2, c.x - z.x2, 0.0)
            dy = max(z.y - c.y2, c.y - z.y2, 0.0)
            gap = max(dx, dy) if (dx == 0.0 or dy == 0.0) else (dx ** 2 + dy ** 2) ** 0.5
            if best is None or gap < best[0]:
                best = (gap, name)
        if best is not None and best[0] < clearance_mm:
            row, col = divmod(i, grid.cols)
            out.append({
                "index": i, "row": row + 1, "col": col + 1,
                "mark": best[1], "gap_mm": round(best[0], 2),
                "text": (f"card {i + 1} (row {row + 1}, column {col + 1}) "
                         f"clears the {best[1]} mark by only "
                         f"{best[0]:.2f} mm — cover it for the scan, then "
                         f"uncover it before the cut starts"),
            })
    out.sort(key=lambda h: h["gap_mm"])
    return out


def _zone_name(zone: Rect, mk: Marks) -> str:
    """Which corner a zone belongs to, in words."""
    vertical = "top" if zone.y < mk.sheet_h_mm / 2 else "bottom"
    horizontal = "left" if zone.x < mk.sheet_w_mm / 2 else "right"
    return f"{vertical}-{horizontal}"


def off_sheet(grid: Grid, mk: Marks) -> list[str]:
    """Marks or cards that fall outside the sheet at all."""
    out: list[str] = []
    page = Rect(0, 0, mk.sheet_w_mm, mk.sheet_h_mm)
    for name, box in (("a registration mark", b) for b in mk.bboxes()):
        if (box.x < 0 or box.y < 0 or box.x2 > page.x2 or box.y2 > page.y2):
            out.append(f"{name} at ({box.x:.2f}, {box.y:.2f}) mm "
                       f"falls off the {mk.sheet_w_mm:g}x{mk.sheet_h_mm:g} mm sheet")
    for i, c in enumerate(grid.cards):
        if c.x < 0 or c.y < 0 or c.x2 > page.x2 or c.y2 > page.y2:
            out.append(f"card {i + 1} at ({c.x:.2f}, {c.y:.2f}) mm falls off "
                       f"the sheet")
    return out


def scan_window(mk: Marks, search_mm: float = 10.0) -> dict:
    """The arguments for the cutter's mark search.

    `search_mm` is how far back from the expected mark position the sensor
    starts hunting; inkscape-silhouette uses 10 mm and clamps at zero, which
    is reproduced here.
    """
    ox, oy = mk.origin_mm
    return {
        "origin_x_mm": ox,
        "origin_y_mm": oy,
        "reg_width_mm": mk.reg_width_mm,
        "reg_length_mm": mk.reg_length_mm,
        "search_top_mm": max(oy - search_mm, 0.0),
        "search_left_mm": max(ox - search_mm, 0.0),
        "quad": mk.pattern == FOUR,
    }
