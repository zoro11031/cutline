"""A settings-finding test sheet: one square per combination, one job.

The Silhouette Card Maker guide's method for finding cut settings is to cut a
test shape, and then - **without ejecting the mat** - lift a corner of the
sheet and look at whether it separated. If it did not, send the same job
again; the number of sends it takes is your pass count. Then move to another
part of the sheet and try different numbers.

That works, but it is slow and it is easy to lose track of which square was
cut with what. This module does the same thing in one pass of the mat: a grid
of small squares, each cut with its own force, speed, depth and pass count,
and a printed map saying which square is which. Peel the sheet once and read
the results off it.

Two deliberate choices:

* **No registration by default.** A settings test does not need marks and
  should not cost a printed sheet - put a blank offcut of the real stock on
  the mat. Pass `registration=True` only if you are testing into a sheet that
  already has marks on it.
* **The same code path as a real cut.** The squares are `CutPath`s and the
  stream is built by the same `CutJob`, so a test sheet is testing the thing
  you are about to cut with, not an approximation of it.

The settings that come out of this are the only ones in the project that may
be labelled `origin: "measured"`.
"""

from __future__ import annotations

import dataclasses
import itertools

from . import graphtec, template
from .geometry import Rect
from .graphtec import CutSettings

# A square big enough to judge an edge on, small enough that a sheet holds a
# useful number of them.
DEFAULT_SIZE_MM = 15.0
DEFAULT_GAP_MM = 5.0
# Keep clear of the mat's own edge and of the rollers.
DEFAULT_MARGIN_MM = 15.0
# Past this the sheet is a maze and the map is useless.
MAX_CELLS = 60


@dataclasses.dataclass(frozen=True)
class Cell:
    """One test square and the settings it is cut with."""

    index: int
    row: int
    col: int
    rect: Rect
    settings: CutSettings

    @property
    def label(self) -> str:
        return f"r{self.row + 1}c{self.col + 1}"

    def describe(self) -> str:
        s = self.settings
        out = f"force {s.force}, speed {s.speed}, {s.passes} pass(es)"
        if s.depth is not None:
            out += f", depth {s.depth}"
        return out


@dataclasses.dataclass
class TestSheet:
    cells: tuple[Cell, ...]
    device: graphtec.Device
    size_mm: float
    registration: bool
    stream: bytes
    steps: list

    @property
    def paths(self) -> tuple:
        return tuple(c_path for c in self.cells
                     for c_path in (_square(c.rect),))

    def map_rows(self) -> list[tuple[str, str]]:
        return [(c.label, c.describe()) for c in self.cells]

    def extent(self) -> Rect:
        xs = [c.rect.x for c in self.cells] + [c.rect.x2 for c in self.cells]
        ys = [c.rect.y for c in self.cells] + [c.rect.y2 for c in self.cells]
        return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _square(rect: Rect) -> template.CutPath:
    return template.CutPath(
        ((rect.x, rect.y), (rect.x2, rect.y),
         (rect.x2, rect.y2), (rect.x, rect.y2)),
        closed=True, label="test square")


def _parse_list(value, cast=int) -> list:
    """"25,29,33" or [25, 29, 33] or 25 -> [25, 29, 33]."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        items = value
    else:
        items = str(value).replace(";", ",").split(",")
    out = []
    for item in items:
        item = str(item).strip()
        if not item:
            continue
        try:
            out.append(cast(float(item)) if cast is int else cast(item))
        except ValueError:
            continue
    return out


def combinations(base: CutSettings, forces=None, speeds=None, passes=None,
                 depths=None) -> list[CutSettings]:
    """Every combination of the axes that were given, in a stable order.

    An axis left empty keeps the base preset's value, so varying one thing at
    a time - which is how the guide says to do it - is the default shape
    rather than a special case.
    """
    f = _parse_list(forces) or [base.force]
    s = _parse_list(speeds) or [base.speed]
    p = _parse_list(passes) or [base.passes]
    d = [None if value == -1 else value for value in _parse_list(depths)] or [base.depth]
    out = []
    for force, speed, npass, depth in itertools.product(f, s, p, d):
        out.append(dataclasses.replace(
            base, force=force, speed=speed, passes=npass, depth=depth))
    return out


def build(device: graphtec.Device, base: CutSettings, forces=None,
          speeds=None, passes=None, depths=None,
          size_mm: float = DEFAULT_SIZE_MM, gap_mm: float = DEFAULT_GAP_MM,
          margin_mm: float = DEFAULT_MARGIN_MM, cols: int = 0,
          sheet_w_mm: float = 215.9, sheet_h_mm: float = 279.4,
          registration: bool = False,
          offset_mm: tuple[float, float] = (0.0, 0.0)) -> TestSheet:
    """Lay out the test squares and build the job.

    Raises `ValueError` rather than producing a sheet that will not fit or a
    map too long to read.
    """
    settings = combinations(base, forces, speeds, passes, depths)
    if not settings:
        raise ValueError("no settings to test")
    if len(settings) > MAX_CELLS:
        raise ValueError(
            f"{len(settings)} combinations is more than a sheet can usefully "
            f"carry (limit {MAX_CELLS}). Vary one axis at a time.")

    pitch = size_mm + gap_mm
    usable_w = sheet_w_mm - 2 * margin_mm
    usable_h = sheet_h_mm - 2 * margin_mm
    max_cols = max(1, int((usable_w + gap_mm) // pitch))
    cols = min(cols, max_cols) if cols > 0 else max_cols
    rows = -(-len(settings) // cols)
    if rows * pitch - gap_mm > usable_h:
        raise ValueError(
            f"{len(settings)} squares of {size_mm:g} mm need "
            f"{rows * pitch - gap_mm:.0f} mm of height but only "
            f"{usable_h:.0f} mm is usable. Use fewer, or a smaller --size.")

    cells = []
    for i, s in enumerate(settings):
        row, col = divmod(i, cols)
        cells.append(Cell(
            index=i, row=row, col=col,
            rect=Rect(margin_mm + col * pitch, margin_mm + row * pitch,
                      size_mm, size_mm),
            settings=s))

    job = graphtec.CutJob(
        device, base, origin_mm=(margin_mm, margin_mm) if registration
        else (0.0, 0.0),
        reg_width_mm=sheet_w_mm - 2 * margin_mm,
        reg_length_mm=sheet_h_mm - 2 * margin_mm,
        registration=registration, offset_mm=offset_mm)
    groups = [(c.settings, [_square(c.rect)]) for c in cells]
    steps = job.plan_matrix(groups)
    stream = b"".join(s.data for s in steps if s.kind == "write")

    return TestSheet(cells=tuple(cells), device=device, size_mm=size_mm,
                     registration=registration, stream=stream, steps=steps)


def write_proof(path, sheet: TestSheet, sheet_w_mm: float,
                sheet_h_mm: float) -> str:
    """An SVG of the squares with their labels, to print or to read on screen.

    This one is a *map*, not a cut file: it carries text, which a cutter
    would try to cut. It exists so the sheet coming off the mat can be read.
    """
    def n(v):
        return f"{v:.3f}".rstrip("0").rstrip(".") or "0"

    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
           f'width="{n(sheet_w_mm)}mm" height="{n(sheet_h_mm)}mm" '
           f'viewBox="0 0 {n(sheet_w_mm)} {n(sheet_h_mm)}">',
           '  <title>cut-proxies test sheet map</title>',
           '  <desc>MAP, NOT A CUT FILE. Do not send this to the cutter.'
           '</desc>',
           f'  <rect x="0" y="0" width="{n(sheet_w_mm)}" '
           f'height="{n(sheet_h_mm)}" fill="#ffffff" stroke="#cccccc" '
           f'stroke-width="0.2"/>']
    for c in sheet.cells:
        r = c.rect
        out.append(f'  <rect x="{n(r.x)}" y="{n(r.y)}" width="{n(r.w)}" '
                   f'height="{n(r.h)}" fill="none" stroke="#ff0000" '
                   f'stroke-width="0.2"/>')
        s = c.settings
        out.append(f'  <text x="{n(r.x + 0.8)}" y="{n(r.y + 3.4)}" '
                   f'font-family="sans-serif" font-size="2.6" '
                   f'fill="#000000">{c.label}</text>')
        out.append(f'  <text x="{n(r.x + 0.8)}" y="{n(r.y + 6.6)}" '
                   f'font-family="sans-serif" font-size="2.2" '
                   f'fill="#444444">F{s.force} S{s.speed} x{s.passes}'
                   f'{f" D{s.depth}" if s.depth is not None else ""}</text>')
    out.append('</svg>')
    text = "\n".join(out) + "\n"
    if path is not None:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
    return text
