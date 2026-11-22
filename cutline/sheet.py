"""Measure the printed sheet, instead of assuming it.

Everything else in this project reasons about where the registration marks
*should* be. Nothing until now looked at where they actually are. That is a
hole in the premise: the whole point of print-and-cut is that the cut matches
the print, and the print was never checked.

It matters because the failure is silent at every earlier stage. The print
can be exported with its registration marks switched off; the sheet can be printed at
"fit to page" and come out 97% of size; the template can be built for a
10 mm inset against a sheet printed at 3.5 mm. In all three the template
verifies perfectly, the job passes every check in `verify.py`, and the first
sign of trouble is a cutter that cannot find its marks - or worse, one that
finds them and cuts a sheet of slightly wrong cards.

So this renders the PDF and finds the marks optically, which is the same
question the Cameo's sensor asks, and compares what is on the paper with
what the template expects.

Method, and its limits. The page is rasterised with `pdftoppm` at a known
dpi, so pixel positions convert back to millimetres exactly. Around each
*expected* mark a search window is opened - deliberately small, so card art
cannot wander into it - and the dark pixels inside it are bounded. For the
5 mm square that box is the mark. For an L the box is the two arms, and the
corner is whichever of its corners faces the paper edge, which is known
from the mark's direction.

What this cannot do is tell you the marks are *readable*: it measures ink
position, not sensor behaviour, and a mark that is grey, or printed over a
dark background, can be found here and missed by the machine. It answers
"are the marks where the template thinks they are", which is the question
that has no other answer.
"""

from __future__ import annotations

import dataclasses
import pathlib
import subprocess
import tempfile

from .geometry import Rect
from .regmarks import Marks

# Enough to resolve a 0.5 mm line (about 4 px) without making a Letter page
# into a 40-megapixel render.
DEFAULT_DPI = 200

# A pixel this dark counts as ink. Registration marks are solid black; the
# threshold is generous so a slightly light print still registers.
INK_THRESHOLD = 128

# How far around an expected mark to look. Big enough to catch a sheet that
# printed a few millimetres out, small enough that a card edge 1.2 mm from
# the mark zone cannot fall inside it on a correct sheet.
SEARCH_MM = 6.0

# Past this, the marks are not where the template believes and the cutter
# will either miss them or cut in the wrong place.
POSITION_TOLERANCE_MM = 0.5

# How far the found ink's footprint may differ from the mark's before we
# stop believing it is the mark at all. Generous, because a search window
# can clip an arm, but far tighter than the difference between a 5 mm
# square and a card corner.
SHAPE_TOLERANCE_MM = 2.0


class SheetError(RuntimeError):
    pass


@dataclasses.dataclass
class FoundMark:
    name: str                       # top-left, top-right, ...
    expected: tuple[float, float]   # mm, the corner the template wants
    measured: tuple[float, float] | None
    ink_box: Rect | None
    kind: str                       # "square" or "ell"
    expected_size_mm: float = 0.0   # the mark's footprint, w and h
    shape_matches: bool = True      # correct silhouette within that footprint

    @property
    def shape_error_mm(self) -> float | None:
        """How far the found ink's footprint is from the mark's.

        Position alone is not identification. A sheet printed with the
        registration option switched off still has card outlines near the
        corners, and taking the nearest ink as "the mark" reported them as
        marks displaced by a few millimetres - a plausible-looking scaling
        diagnosis for a sheet that simply has no marks on it. The size of
        the ink is what tells a 5 mm square from the corner of a card.
        """
        if self.ink_box is None or not self.expected_size_mm:
            return None
        return max(abs(self.ink_box.w - self.expected_size_mm),
                   abs(self.ink_box.h - self.expected_size_mm))

    @property
    def delta_mm(self) -> tuple[float, float] | None:
        if self.measured is None:
            return None
        return (self.measured[0] - self.expected[0],
                self.measured[1] - self.expected[1])

    @property
    def error_mm(self) -> float | None:
        d = self.delta_mm
        return None if d is None else max(abs(d[0]), abs(d[1]))

    @property
    def is_credible(self) -> bool:
        """Whether the ink found looks like the mark we were looking for."""
        err = self.shape_error_mm
        return self.measured is not None and self.shape_matches and (
            err is None or err <= SHAPE_TOLERANCE_MM)

    def describe(self) -> str:
        if self.measured is None:
            return f"{self.name}: no ink found in the search window"
        if not self.shape_matches and self.shape_error_mm <= SHAPE_TOLERANCE_MM:
            return f"{self.name}: nearby ink does not have the expected mark shape"
        if not self.is_credible:
            b = self.ink_box
            return (f"{self.name}: found ink {b.w:.1f} x {b.h:.1f} mm where "
                    f"a {self.expected_size_mm:.1f} mm mark was expected - "
                    f"this is not that mark")
        dx, dy = self.delta_mm
        return (f"{self.name}: expected ({self.expected[0]:.2f}, "
                f"{self.expected[1]:.2f}) found ({self.measured[0]:.2f}, "
                f"{self.measured[1]:.2f}) off by {dx:+.2f}, {dy:+.2f} mm")


@dataclasses.dataclass
class SheetReport:
    pdf: str
    page: int
    page_w_mm: float
    page_h_mm: float
    dpi: int
    marks: list[FoundMark]
    preview_rotation: int = 0

    @property
    def found(self) -> list[FoundMark]:
        """Marks whose ink is both present and the right shape."""
        return [m for m in self.marks if m.is_credible]

    @property
    def missing(self) -> list[FoundMark]:
        return [m for m in self.marks if m.measured is None]

    @property
    def wrong_shape(self) -> list[FoundMark]:
        return [m for m in self.marks
                if m.measured is not None and not m.is_credible]

    @property
    def worst_error_mm(self) -> float | None:
        errs = [m.error_mm for m in self.found]
        return max(errs) if errs else None

    def problems(self, sheet_w_mm: float, sheet_h_mm: float,
                 tolerance_mm: float = POSITION_TOLERANCE_MM) -> list[str]:
        """Everything that should stop this sheet being cut."""
        out: list[str] = []
        if abs(self.page_w_mm - sheet_w_mm) > 1.0 \
                or abs(self.page_h_mm - sheet_h_mm) > 1.0:
            out.append(
                f"the PDF page is {self.page_w_mm:.1f} x "
                f"{self.page_h_mm:.1f} mm but the template is for "
                f"{sheet_w_mm:.1f} x {sheet_h_mm:.1f} mm")
        if not self.found:
            out.append(
                "no registration marks were found anywhere this template "
                "expects them. The sheet was probably exported with its "
                "registration marks switched off.")
        for m in self.missing:
            out.append(m.describe())
        for m in self.wrong_shape:
            out.append(m.describe())
        for m in self.found:
            if m.error_mm > tolerance_mm:
                out.append(m.describe())
        # A consistent shift is a different diagnosis from a scattered one.
        if len(self.found) > 1:
            dxs = [m.delta_mm[0] for m in self.found]
            dys = [m.delta_mm[1] for m in self.found]
            spread = max(max(dxs) - min(dxs), max(dys) - min(dys))
            if spread > 2 * tolerance_mm:
                out.append(
                    f"PDF mark displacement varies by {spread:.2f} mm. "
                    "A single X/Y offset cannot correct the scaling or rotation mismatch.")
        return out

    def diagnosis(self) -> str:
        """One line on what the measurements look like, if anything."""
        if not self.found:
            if self.wrong_shape:
                return ("no registration marks on this sheet - the ink near "
                        "the corners is the wrong size and shape for a mark, "
                        "most likely card art")
            return "no marks found"
        dxs = [m.delta_mm[0] for m in self.found]
        dys = [m.delta_mm[1] for m in self.found]
        spread = max(max(dxs) - min(dxs), max(dys) - min(dys))
        shift = max(abs(sum(dxs) / len(dxs)), abs(sum(dys) / len(dys)))
        if spread > 1.0:
            return ("The PDF's registration marks are scaled or rotated "
                    "relative to the template.")
        if shift > POSITION_TOLERANCE_MM:
            return (f"marks displaced together by about "
                    f"{sum(dxs) / len(dxs):+.2f}, {sum(dys) / len(dys):+.2f} "
                    f"mm: a uniform PDF offset, correctable")
        return "marks are where the template expects them"


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def page_size_mm(pdf) -> tuple[float, float, int]:
    """Page size in mm and the page count, via pdfinfo."""
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True,
                         text=True, check=False)
    if out.returncode != 0:
        raise SheetError(f"not a readable PDF: {pdf}")
    w = h = 0.0
    pages = 0
    for line in out.stdout.splitlines():
        if line.startswith("Page size:"):
            parts = line.split()
            w, h = float(parts[2]), float(parts[4])
        elif line.startswith("Pages:"):
            pages = int(line.split()[1])
    # pdfinfo reports points.
    return w * 25.4 / 72.0, h * 25.4 / 72.0, pages


def render(pdf, page: int = 1, dpi: int = DEFAULT_DPI, colour: bool = False):
    """Rasterise one page to a PIL image at a known dpi."""
    try:
        from PIL import Image
    except ImportError as exc:                 # pragma: no cover
        raise SheetError("Pillow is needed to measure a printed sheet") \
            from exc
    with tempfile.TemporaryDirectory(prefix="cutline-sheet.") as tmp:
        stem = pathlib.Path(tmp) / "page"
        out = subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page)]
            + ([] if colour else ['-gray']) + ["-png", str(pdf), str(stem)],
            capture_output=True, check=False)
        if out.returncode != 0:
            raise SheetError(
                f"pdftoppm could not render page {page}: "
                f"{out.stderr.decode('utf-8', 'replace')[:200]}")
        pngs = sorted(pathlib.Path(tmp).glob("page*.png"))
        if not pngs:
            raise SheetError(f"pdftoppm produced nothing for page {page}")
        with Image.open(pngs[0]) as im:
            return im.convert("RGB" if colour else "L").copy()


# --------------------------------------------------------------------------
# finding marks
# --------------------------------------------------------------------------

def ink_blobs(image, window: Rect, dpi: int,
              threshold: int = INK_THRESHOLD) -> list[Rect]:
    """Bounding boxes of each separate patch of ink in a window, in mm.

    Not one box around all the ink. A registration mark sits about 1.2 mm
    from the nearest card on a full sheet (by design in the layout), and a
    search window wide enough to find a mark on a misprinted sheet is wide
    enough to contain that card's edge. Bounding all the ink together
    merges the two: measuring a correct sheet that way gave an 8 mm arm a
    height of 12.95 mm, and the shape check then rejected a perfectly good
    sheet.

    So the ink is separated into connected components first, and the caller
    picks the one that looks like the mark. The windows are small - a 20 mm
    square at 200 dpi is about 25k pixels - so a plain breadth-first scan
    is cheap enough and avoids a dependency.
    """
    px = dpi / 25.4
    left = max(0, int(window.x * px))
    top = max(0, int(window.y * px))
    right = min(image.width, int(round(window.x2 * px)))
    bottom = min(image.height, int(round(window.y2 * px)))
    if right <= left or bottom <= top:
        return []
    crop = image.crop((left, top, right, bottom))
    w, h = crop.size
    ink = [v < threshold for v in crop.getdata()]
    if not any(ink):
        return []

    seen = bytearray(w * h)
    out: list[Rect] = []
    for start in range(w * h):
        if not ink[start] or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        x0 = x1 = start % w
        y0 = y1 = start // w
        while stack:
            i = stack.pop()
            x, y = i % w, i // w
            if x < x0: x0 = x
            if x > x1: x1 = x
            if y < y0: y0 = y
            if y > y1: y1 = y
            # 8-connected: a 1 px diagonal join is still one mark.
            for dy in (-1, 0, 1):
                ny = y + dy
                if ny < 0 or ny >= h:
                    continue
                for dx in (-1, 0, 1):
                    nx = x + dx
                    if nx < 0 or nx >= w:
                        continue
                    j = ny * w + nx
                    if ink[j] and not seen[j]:
                        seen[j] = 1
                        stack.append(j)
        out.append(Rect((left + x0) / px, (top + y0) / px,
                        (x1 - x0 + 1) / px, (y1 - y0 + 1) / px))
    return out


def best_blob(blobs: list[Rect], size_mm: float,
              tolerance_mm: float, shape_matches=lambda box: True) -> Rect | None:
    """The patch of ink that best matches a mark of this footprint.

    Matched on size rather than on proximity, because the nearest ink to a
    mark's expected position is a card edge on a sheet that has no marks at
    all - which is how "no registration marks" was first mis-reported as
    "marks displaced by a few millimetres".
    """
    scored = []
    for b in blobs:
        err = max(abs(b.w - size_mm), abs(b.h - size_mm))
        plausible = err <= tolerance_mm and shape_matches(b)
        scored.append((not plausible, err, b))
    if not scored:
        return None
    scored.sort(key=lambda entry: entry[:2])
    _, _, box = scored[0]
    # Still return the best candidate when nothing fits: the caller reports
    # "found ink of the wrong shape", which is more useful than silence.
    return box


def mark_ink_matches(image, box: Rect, dpi: int, ell=None) -> bool:
    """Distinguish the filled square and oriented L from same-size artwork."""
    pixels = dpi / 25.4
    crop = image.crop(tuple(round(mm * pixels) for mm in (box.x, box.y, box.x2, box.y2)))
    width, height = crop.size

    def filled(region):
        histogram = crop.crop(region).histogram()
        return sum(histogram[:INK_THRESHOLD]) / max(sum(histogram), 1)

    if ell is None:
        return filled((0, 0, width, height)) >= 0.8
    band = max(1, round(ell.thickness * pixels) + 2)
    x = 0 if ell.dx > 0 else width - band
    y = 0 if ell.dy > 0 else height - band
    inner_x0, inner_x1 = (band, width) if ell.dx > 0 else (0, width-band)
    inner_y0, inner_y1 = (band, height) if ell.dy > 0 else (0, height-band)
    if inner_x1 <= inner_x0 or inner_y1 <= inner_y0:
        return False
    # Allow small print rotations and antialiasing while requiring both arms
    # at the outside of the corner and a mostly white interior.
    return (filled((0, y, width, y+band)) >= 0.35
            and filled((x, 0, x+band, height)) >= 0.35
            and filled((inner_x0, inner_y0, inner_x1, inner_y1)) <= 0.25)


def measure_preview(pdf, marks, page=1, dpi=DEFAULT_DPI, *, orient_scm=False):
    """Render an overlay in cutter coordinates and measure its registration marks."""
    image = render(pdf, page=page, dpi=dpi, colour=True)
    turn = 0
    if orient_scm:
        nominal = tuple(edge * dpi / 25.4 for edge in (marks.sheet_w_mm, marks.sheet_h_mm))

        def matches(size):
            return all(abs(actual - expected) <= 1.5 for actual, expected in zip(size, nominal))

        if not matches(image.size) and matches(image.size[::-1]):
            turn = 270 if marks.sheet_w_mm < marks.sheet_h_mm else 90
            image = image.rotate(turn, expand=True)
    report = measure(pdf, marks, page=page, dpi=dpi, image=image, swap_page_axes=bool(turn))
    report.preview_rotation = turn
    return report, image


def measure(pdf, mk: Marks, page: int = 1, dpi: int = DEFAULT_DPI,
            search_mm: float = SEARCH_MM, image=None, swap_page_axes=False, *,
            expected_pages: int | None = None) -> SheetReport:
    """Find each expected mark on the page and say where it actually is."""
    page_w, page_h, pages = page_size_mm(pdf)
    if expected_pages is not None and pages != expected_pages:
        raise SheetError(f"The PDF contains {pages} page(s), but the bundle maps "
                         f"{expected_pages}. Download the matching print and cut files again.")
    if swap_page_axes:
        page_w, page_h = page_h, page_w
    if page < 1 or page > pages:
        raise SheetError(f"page {page} is outside the PDF's 1..{pages}")
    image = render(pdf, page, dpi) if image is None else image.convert('L')

    found: list[FoundMark] = []

    def corner_name(x, y):
        return (("top" if y < page_h / 2 else "bottom") + "-"
                + ("left" if x < page_w / 2 else "right"))

    if mk.square is not None:
        s = mk.square
        window = s.grown(search_mm)
        square_shape = lambda box: mark_ink_matches(image, box, dpi)
        box = best_blob(ink_blobs(image, window, dpi), s.w,
                        SHAPE_TOLERANCE_MM, square_shape)
        # Some tools fill a 5 mm square; Card Maker also strokes its border.
        # Identify the footprint by size alone so actual displacement still
        # appears in the report for either renderer.
        weight = mk.thickness_mm
        edged = box is not None and min(box.w, box.h) > s.w + weight/2
        pad = weight/2 if edged else 0
        found.append(FoundMark(
            name=corner_name(s.x, s.y) + " square",
            expected=(s.x, s.y),
            measured=(box.x+pad, box.y+pad) if box else None,
            ink_box=box, kind="square", expected_size_mm=s.w+2*pad,
            shape_matches=box is None or square_shape(box)))

    for ell in mk.ells:
        cx, cy = ell.corner_x, ell.corner_y
        # The arms run inward from the corner, so the ink lives between the
        # corner and (corner + direction * length).
        x0 = min(cx, cx + ell.dx * ell.length)
        y0 = min(cy, cy + ell.dy * ell.length)
        window = Rect(x0, y0, ell.length, ell.length).grown(search_mm)
        ell_shape = lambda box: mark_ink_matches(image, box, dpi, ell)
        box = best_blob(ink_blobs(image, window, dpi), ell.length,
                        SHAPE_TOLERANCE_MM, ell_shape)
        measured = None
        if box is not None:
            # The corner is the end of the box facing the paper edge, pulled
            # back in by half the line width.
            #
            # An L's arms are strokes centred on their centre-lines, so the
            # ink reaches thickness/2 beyond the nominal corner on the
            # outside. Taking the raw ink edge reports every correct sheet
            # as displaced outward by exactly half a line width - 0.47 mm
            # on a 1 mm line, which sits just inside the 0.5 mm tolerance
            # and so would have passed while being wrong.
            half = ell.thickness / 2.0
            measured = ((box.x + half) if ell.dx > 0 else (box.x2 - half),
                        (box.y + half) if ell.dy > 0 else (box.y2 - half))
        found.append(FoundMark(
            name=corner_name(cx, cy) + " L",
            expected=(cx, cy), measured=measured, ink_box=box, kind="ell",
            expected_size_mm=ell.length, shape_matches=box is None or ell_shape(box)))

    return SheetReport(pdf=str(pdf), page=page, page_w_mm=page_w,
                       page_h_mm=page_h, dpi=dpi, marks=found)


class RegistrationDecision:
    NONE = "none"
    CHECKING = "checking"
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"

    @classmethod
    def evaluate(cls, state: str, has_pdf: bool, registration_on: bool) -> tuple[bool, str]:
        """Determine whether hardware actions (Send to Cutter, Corner Test, Test Registration) are allowed.
        Returns (allowed: bool, refusal_reason: str).
        """
        if not registration_on or not has_pdf:
            return True, ""
        if state == cls.CHECKING:
            return False, "Registration check pending. Open Sheet to review."
        if state == cls.FAILED:
            return False, "Registration check failed. Open Sheet to see the missing marks."
        if state == cls.ERROR:
            return False, "Could not measure registration marks on the print file. Open Sheet to review."
        if state == cls.PASSED:
            return True, ""
        return False, "Check the matching PDF in Sheet."
