"""Sheet, card and grid geometry in millimetres.

Everything here works in mm with the origin at the **top-left of the sheet**,
x to the right and y down, which is the convention both the PDF and Silhouette
Studio use.  The conversion to the cutter's own axes happens once, at the
protocol boundary in `graphtec.py`, and nowhere else.

Two layout engines live here, because there are two different things a cut
template can have to line up with:

`scm_layout`
    The Silhouette Card Maker algorithm, reimplemented from its
    `page_manager.py` (`select_best_margins` / `compute_card_positions` /
    `generate_layout`).  It works in integer pixels at a fixed ppi, which is
    what makes its output reproducible, and it refuses a layout rather than
    quietly returning one that fouls a registration mark.  Our implementation
    reproduces every published number in SCM's own `layouts.json` exactly -
    see `tests/test_geometry.py::test_matches_scm_published_layouts`, which is
    the test that matters most in this project.

`centered_layout`
    A grid centred on the whole sheet, the way most card-printing tools lay
    out a page.  Use this when the sheet came out of such a tool, because
    then the cut has to match the print that exists, not the print we would
    have preferred.

The two do not agree, and they are not meant to: SCM centres cards in a
*usable area* inset from the paper edge and spaces them by a fixed 1.25 mm,
while the centred grid sits on the *whole sheet* and takes bleed, gap
and margin from the user.  `cutline` therefore always records which engine
produced a template, and `verify.py` refuses to cut a template against a
mismatched sheet.
"""

from __future__ import annotations

import dataclasses
import math

# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

MM_PER_INCH = 25.4

# SCM works in integer pixels at 300 ppi; that quantisation is part of the
# algorithm's output, not an implementation detail, so it is reproduced.
SCM_PPI = 300

# SCM: page_manager.generate_layout, CARD_DISTANCE = "1.25mm".  The gap
# between adjacent card faces; the usable bleed is half of it.
SCM_CARD_DISTANCE_MM = 1.25


def mm_to_px(mm: float, ppi: int = SCM_PPI) -> int:
    """SCM's `size_convert.size_to_pixel` for a mm value."""
    return round(mm / MM_PER_INCH * ppi)


def px_to_mm(px: float, ppi: int = SCM_PPI) -> float:
    return px * MM_PER_INCH / ppi


# --------------------------------------------------------------------------
# Sheets and cards
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Sheet:
    """A paper size, stored portrait (width <= height).

    `printable` says whether an ordinary desktop printer can feed it. A
    sheet the Cameo could happily cut is no use if it cannot be printed, so
    the recommendations filter on this rather than offering a layout that
    cannot exist.
    """

    name: str
    portrait_width_mm: float
    portrait_height_mm: float
    printable: bool = True
    # Whether the card stock this suite uses is actually sold in this size.
    # ProxyJak comes in Letter and A4 and nothing else, so a layout on any
    # other sheet is a layout for paper that cannot be bought - which is
    # worse than no recommendation, because it reads like advice.
    stocked: bool = False

    def size(self, portrait: bool = True) -> tuple[float, float]:
        w, h = self.portrait_width_mm, self.portrait_height_mm
        return (w, h) if portrait else (h, w)


# Letter and A4 are the two the printer in this suite is set up for.  Legal and
# Tabloid are here because SCM supports them and the geometry is free, but
# nothing downstream has been tested on them.
SHEETS: dict[str, Sheet] = {
    "Letter": Sheet("Letter", 215.9, 279.4, stocked=True),
    "A4": Sheet("A4", 210.0, 297.0, stocked=True),
    "Legal": Sheet("Legal", 215.9, 355.6),
    # Desktop printers stop well short of Tabloid; it is here because the
    # Cameo can cut it and someone may print elsewhere.
    "Tabloid": Sheet("Tabloid", 279.4, 431.8, printable=False),
    "A3": Sheet("A3", 297.0, 420.0, printable=False),
    "Arch B": Sheet("Arch B", 304.8, 457.2, printable=False),
}


def match_sheet(dimensions: tuple[float, float]) -> tuple[str, bool] | None:
    """Recognize a named paper size and orientation from measured millimetres."""
    for paper in SHEETS.values():
        for portrait in (True, False):
            if all(abs(a - b) <= 0.1 for a, b in zip(dimensions, paper.size(portrait))):
                return paper.name, portrait
    return None


@dataclasses.dataclass(frozen=True)
class Card:
    """A card face.  `radius_mm` is the printed corner radius."""

    name: str
    width_mm: float
    height_mm: float
    radius_mm: float = 3.0


# Sizes and radii from SCM's layouts.json `card_sizes`.  "standard" is the
# 63x88 trading-card face.
CARDS: dict[str, Card] = {
    "standard": Card("standard", 63.0, 88.0, 3.0),
    "poker": Card("poker", 63.5, 88.9, 3.0),
    "japanese": Card("japanese", 59.0, 86.0, 1.0),
    "tarot": Card("tarot", 70.0, 120.0, 3.0),
    "catan": Card("catan", 54.0, 80.0, 3.0),
    "euro_mini": Card("euro_mini", 44.0, 68.0, 3.0),
    "american_mini": Card("american_mini", 41.0, 63.0, 3.0),
}

# Other common names for them, so a user can say either.
CARD_ALIASES = {"mtg": "standard", "euro_poker": "standard", "mini_euro": "euro_mini"}


def card(name: str) -> Card:
    key = CARD_ALIASES.get(name.lower(), name.lower())
    if key not in CARDS:
        raise KeyError(
            f"unknown card size {name!r}; known: {', '.join(sorted(CARDS))}")
    return CARDS[key]


def sheet(name: str) -> Sheet:
    for key, value in SHEETS.items():
        if key.lower() == name.lower():
            return value
    raise KeyError(
        f"unknown sheet {name!r}; known: {', '.join(SHEETS)}")


# --------------------------------------------------------------------------
# A laid-out grid
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Rect:
    """An axis-aligned rectangle in mm, top-left origin."""

    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    def grown(self, by: float) -> "Rect":
        return Rect(self.x - by, self.y - by, self.w + 2 * by, self.h + 2 * by)

    def overlaps(self, other: "Rect", tol: float = 1e-9) -> bool:
        return (self.x < other.x2 - tol and other.x < self.x2 - tol
                and self.y < other.y2 - tol and other.y < self.y2 - tol)


# How each layout engine is named to a person, rather than by its id.
ENGINE_NAMES = {"scm": "Card Maker layout", "centered": "centred grid", "imported": "from the file"}


@dataclasses.dataclass(frozen=True)
class Grid:
    """Where every card face sits on one sheet.

    `cards` is in reading order: left to right, then top to bottom, which is
    the order a print layout fills slots and the order the cut is
    emitted in unless a cut strategy reorders it.
    """

    engine: str                 # "scm" or "centered"
    sheet_name: str
    portrait: bool
    sheet_w_mm: float
    sheet_h_mm: float
    card: Card
    cols: int
    rows: int
    cards: tuple[Rect, ...]
    card_distance_mm: float
    # The largest registration-mark arm length that still clears the grid.
    # SCM reports this per layout; it is advisory, not a constraint.
    max_mark_length_mm: float

    @property
    def count(self) -> int:
        return len(self.cards)

    @property
    def bounds(self) -> Rect:
        """The bounding box of every card face (not including bleed)."""
        x = min(c.x for c in self.cards)
        y = min(c.y for c in self.cards)
        return Rect(x, y,
                    max(c.x2 for c in self.cards) - x,
                    max(c.y2 for c in self.cards) - y)

    def describe(self) -> str:
        o = "portrait" if self.portrait else "landscape"
        layout = ENGINE_NAMES.get(self.engine, self.engine)
        return (f"{self.cols}x{self.rows} = {self.count} {self.card.name} cards "
                f"on {self.sheet_name} {o} ({layout})")


# --------------------------------------------------------------------------
# Engine 1: Silhouette Card Maker
# --------------------------------------------------------------------------

def _scm_grid_fit(usable_px: int, card_px: int, bleed_px: int) -> int:
    """SCM `page_manager.compute_grid_fit`.

    Bleed may hang off the usable area; only the card faces have to be inside
    it, hence the `+ bleed` on the numerator.
    """
    if usable_px <= 0:
        return 0
    return max(0, math.floor((usable_px + bleed_px) / (card_px + bleed_px)))


def _scm_best_margins(page_w: int, page_h: int, card_w: int, card_h: int,
                      bleed: int, inset: int, corner_len: int):
    """SCM `page_manager.select_best_margins`, in integer pixels.

    The corner rule: a layout is invalid when the grid comes within
    `corner_len` of the inset boundary on *both* axes at once, because that is
    the square region a registration mark occupies.  Clearing one axis is
    enough, which is why the three margin strategies and the two
    shrink-one-axis candidates exist.
    """
    strategies = (
        (inset, inset),                 # minimal margins
        (inset + corner_len, inset),    # clear the corners horizontally
        (inset, inset + corner_len),    # clear the corners vertically
    )
    best = None
    best_count = 0

    def record(cols, rows, mx, my, uw, uh):
        nonlocal best, best_count
        if cols <= 0 or rows <= 0:
            return
        grid_w = cols * card_w + (cols + 1) * bleed
        grid_h = rows * card_h + (rows + 1) * bleed
        gap_x = mx + (uw - grid_w) / 2 - inset
        gap_y = my + (uh - grid_h) / 2 - inset
        if gap_x < corner_len and gap_y < corner_len:
            return
        if cols * rows > best_count:
            best_count = cols * rows
            best = (cols, rows, mx, my, uw, uh)

    for mx, my in strategies:
        uw, uh = page_w - 2 * mx, page_h - 2 * my
        max_cols = _scm_grid_fit(uw, card_w, bleed)
        max_rows = _scm_grid_fit(uh, card_h, bleed)
        if max_cols == 0 or max_rows == 0:
            continue
        record(max_cols, max_rows, mx, my, uw, uh)
        cols_clear = max(0, math.floor(
            (uw - bleed - 2 * (corner_len - mx + inset)) / (card_w + bleed)))
        rows_clear = max(0, math.floor(
            (uh - bleed - 2 * (corner_len - my + inset)) / (card_h + bleed)))
        record(cols_clear, max_rows, mx, my, uw, uh)
        record(max_cols, rows_clear, mx, my, uw, uh)

    if best is None:
        raise LayoutImpossible(
            "No layout fits without a card or its bleed intruding into a "
            "registration-mark corner zone.")
    return best


class LayoutImpossible(ValueError):
    """Raised instead of returning a layout that fouls a registration mark."""


def scm_layout(sheet_name: str = "Letter", card_name: str = "standard",
               portrait: bool | None = None, inset_mm: float = 10.0,
               probe_length_mm: float = 5.0,
               card_distance_mm: float = SCM_CARD_DISTANCE_MM,
               ppi: int = SCM_PPI) -> Grid:
    """Lay out a sheet the way Silhouette Card Maker does.

    `portrait=None` tries both and keeps whichever fits more cards, which is
    SCM's "orientation optimization".  `probe_length_mm` is the mark arm
    length used *while choosing* the layout (SCM's default 5 mm); the longest
    arm the chosen layout can actually carry comes back as
    `Grid.max_mark_length_mm`.

    Raises `LayoutImpossible` when nothing fits clear of the marks.
    """
    sh = sheet(sheet_name)
    cd = card(card_name)

    candidates = [True, False] if portrait is None else [portrait]
    best: Grid | None = None
    for is_portrait in candidates:
        try:
            grid = _scm_layout_one(sh, cd, is_portrait, inset_mm,
                                   probe_length_mm, card_distance_mm, ppi)
        except LayoutImpossible:
            continue
        # More cards wins; a tie goes to the first orientation tried, so that
        # an explicit request is never silently flipped.
        if best is None or grid.count > best.count:
            best = grid
    if best is None:
        raise LayoutImpossible(
            f"No {card_name} layout fits on {sheet_name} with a {inset_mm} mm "
            f"registration inset.")
    return best


def _scm_layout_one(sh: Sheet, cd: Card, portrait: bool, inset_mm: float,
                    probe_length_mm: float, card_distance_mm: float,
                    ppi: int) -> Grid:
    # SCM stores paper landscape and swaps for portrait; card dimensions are
    # never swapped, so a "rotated" card layout is expressed by the page
    # orientation rather than by turning the cards.
    page_w_mm, page_h_mm = sh.size(portrait)
    page_w = mm_to_px(page_w_mm, ppi)
    page_h = mm_to_px(page_h_mm, ppi)
    card_w = mm_to_px(cd.width_mm, ppi)
    card_h = mm_to_px(cd.height_mm, ppi)
    bleed = mm_to_px(card_distance_mm, ppi)
    inset = mm_to_px(inset_mm, ppi)
    corner_len = mm_to_px(probe_length_mm, ppi)

    cols, rows, mx, my, uw, uh = _scm_best_margins(
        page_w, page_h, card_w, card_h, bleed, inset, corner_len)

    grid_w = cols * card_w + (cols + 1) * bleed
    grid_h = rows * card_h + (rows + 1) * bleed
    start_x = round(mx + (uw - grid_w) / 2 + bleed)
    start_y = round(my + (uh - grid_h) / 2 + bleed)

    cards = tuple(
        Rect(px_to_mm(start_x + c * (card_w + bleed), ppi),
             px_to_mm(start_y + r * (card_h + bleed), ppi),
             px_to_mm(card_w, ppi), px_to_mm(card_h, ppi))
        for r in range(rows) for c in range(cols))

    # SCM: the longest arm that still leaves one card-distance of slack
    # between the mark and the nearest card.
    max_len_px = max(0, max(start_x - inset, start_y - inset) - bleed)

    return Grid(
        engine="scm", sheet_name=sh.name, portrait=portrait,
        sheet_w_mm=page_w_mm, sheet_h_mm=page_h_mm, card=cd,
        cols=cols, rows=rows, cards=cards,
        card_distance_mm=card_distance_mm,
        max_mark_length_mm=round(px_to_mm(max_len_px, ppi), 2),
    )


# --------------------------------------------------------------------------
# Engine 2: centred grid
# --------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class CenteredGridSettings:
    """The centred-grid layout settings that move the cards.

    `rotation` "auto" picks whichever of upright or side-on fits more cards.
    """

    card_w_mm: float = 63.0
    card_h_mm: float = 88.0
    bleed_mm: float = 1.0
    gap_mm: float = 0.0
    margin_mm: float = 4.0
    cols: int = 0               # 0 means "as many as fit"
    rows: int = 0
    rotation: str = "auto"      # auto | upright | rotated
    # 3.2 mm, not the 3 mm that Silhouette Card Maker uses. Cutting a sheet
    # printed with 3.2 mm corners to a 3 mm radius leaves a visibly different
    # corner from the printed guide.
    corner_radius_mm: float = 3.2


def centered_layout(sheet_name: str = "Letter", portrait: bool = False,
                    settings: CenteredGridSettings | None = None) -> Grid:
    """A grid centred on the **whole sheet**, not on an inset usable area,
    with each card face `bleed` inside its slot.
    """
    s = settings or CenteredGridSettings()
    sh = sheet(sheet_name)
    W, H = sh.size(portrait)

    def fit(cw: float, ch: float):
        sw, sh_ = cw + 2 * s.bleed_mm, ch + 2 * s.bleed_mm
        cols = s.cols if s.cols > 0 else max(1, math.floor(
            (W - 2 * s.margin_mm + s.gap_mm) / (sw + s.gap_mm)))
        rows = s.rows if s.rows > 0 else max(1, math.floor(
            (H - 2 * s.margin_mm + s.gap_mm) / (sh_ + s.gap_mm)))
        gw = cols * sw + (cols - 1) * s.gap_mm
        gh = rows * sh_ + (rows - 1) * s.gap_mm
        # The grid may reach to within `edge` of the paper.
        edge = 2 * min(s.margin_mm, 3)
        fits = gw <= W - edge + 0.01 and gh <= H - edge + 0.01
        return dict(cw=cw, ch=ch, sw=sw, sh=sh_, cols=cols, rows=rows,
                    per=cols * rows, gw=gw, gh=gh, fits=fits)

    up = fit(s.card_w_mm, s.card_h_mm)
    side = fit(s.card_h_mm, s.card_w_mm)
    use_side = (s.rotation == "rotated"
                or (s.rotation == "auto" and side["fits"]
                    and (not up["fits"] or side["per"] > up["per"])))
    g = side if use_side else up

    x0 = (W - g["gw"]) / 2
    y0 = (H - g["gh"]) / 2
    cards = tuple(
        Rect(x0 + c * (g["sw"] + s.gap_mm) + s.bleed_mm,
             y0 + r * (g["sh"] + s.gap_mm) + s.bleed_mm,
             g["cw"], g["ch"])
        for r in range(g["rows"]) for c in range(g["cols"]))

    # The face presented to the cutter, which is the card turned on its side
    # when the sheet is laid out that way.
    face = Card(f"{s.card_w_mm:g}x{s.card_h_mm:g}",
                g["cw"], g["ch"], s.corner_radius_mm)

    return Grid(
        engine="centered", sheet_name=sh.name, portrait=portrait,
        sheet_w_mm=W, sheet_h_mm=H, card=face,
        cols=g["cols"], rows=g["rows"], cards=cards,
        card_distance_mm=s.gap_mm + 2 * s.bleed_mm,
        # A centred grid has no arm limit of its own; report the slack that exists.
        max_mark_length_mm=round(max(0.0, min(x0, y0)), 2),
    )
