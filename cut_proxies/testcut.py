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
import math

from . import graphtec, template, toolpath, verify
from .geometry import Rect
from .graphtec import CutSettings
from .job import cutting_mat_errors

# A square big enough to judge an edge on, small enough that a sheet holds a
# useful number of them.
DEFAULT_SIZE_MM = 15.0
DEFAULT_GAP_MM = 5.0
# Keep clear of the mat's own edge and of the rollers.
DEFAULT_MARGIN_MM = 15.0
# Past this the sheet is a maze and the map is useless.
MAX_CELLS = 60


TEST_SHAPES = {
    "square": "Square",
    "rounded-square": "Rounded square",
    "card": "Rounded card",
    "circle": "Circle",
    "oval": "Oval",
    "ring": "Ring",
    "triangle": "Triangle",
    "diamond": "Diamond",
    "hexagon": "Hexagon",
    "star": "Star",
    "notch": "Notched square",
    "studio": "Studio square and triangle",
}
# Captured Studio test cut documented in inkscape-silhouette/sendto_silhouette.py.
# Coordinates include the 1 mm seam overlaps and 1.5 mm inset.
STUDIO_TRIANGLE = ((6.0, 4.577), (6.5, 3.711), (9.721, 9.289),
                   (3.279, 9.289), (6.5, 3.711), (7.0, 4.577))
STUDIO_SQUARE = ((2.5, 1.5), (1.5, 1.5), (1.5, 11.5), (11.5, 11.5),
                 (11.5, 1.5), (1.5, 1.5), (1.5, 2.5))


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
    shape: str = "square"

    @property
    def paths(self) -> tuple:
        return tuple(path for cell in self.cells for path in _cell_paths(cell.rect, self.shape))

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


def _cell_paths(rect: Rect, shape: str) -> tuple[template.CutPath, ...]:
    if shape == "square":
        return (_square(rect),)
    if shape == "studio":
        scale = rect.w / 10
        return tuple(template.CutPath(
            tuple((rect.x + (x - 1.5) * scale, rect.y + (y - 1.5) * scale) for x, y in outline),
            closed=False, label=label)
            for outline, label in ((STUDIO_TRIANGLE, "test triangle"), (STUDIO_SQUARE, "test square")))
    label = TEST_SHAPES[shape]
    if shape in ("card", "rounded-square"):
        width = rect.w * 63 / 88 if shape == "card" else rect.w
        box = Rect(rect.x + (rect.w - width) / 2, rect.y, width, rect.h)
        radius = rect.h * (3.2 / 88 if shape == "card" else 0.2)
        return (template.CutPath(template.rounded_rect_points(box, radius), True, label=label),)
    if shape in ("circle", "oval", "ring"):
        radii = (0.5, 0.32 if shape == "oval" else 0.5)
        outer = _ellipse(rect, *radii, label)
        return (_ellipse(rect, 0.26, 0.26, "Ring center"), outer) if shape == "ring" else (outer,)
    if shape == "notch":
        vertices = ((0, 0), (1, 0), (1, 0.42), (0.55, 0.42), (0.55, 1), (0, 1))
    else:
        count = {"triangle": 3, "diamond": 4, "hexagon": 6, "star": 10}[shape]
        vertices = []
        for index in range(count):
            angle = index * math.tau / count
            radius = 0.24 if shape == "star" and index % 2 else 0.5
            vertices.append((0.5 + radius * math.sin(angle), 0.5 - radius * math.cos(angle)))
    points = tuple((rect.x + x * rect.w, rect.y + y * rect.h) for x, y in vertices)
    return (template.CutPath(points, True, label=label),)


def _ellipse(rect, rx, ry, label):
    count = template._arc_segments(max(rx * rect.w, ry * rect.h), math.tau)
    points = tuple((rect.x + rect.w * (0.5 + rx * math.cos(angle)),
                    rect.y + rect.h * (0.5 + ry * math.sin(angle)))
                   for angle in (i * math.tau / count for i in range(count)))
    return template.CutPath(points, True, label=label)


def _check_geometry(stream, groups, device, origin, limit):
    """Read every plotted vertex back, including every pass of every cell."""
    ox, oy = origin
    wanted = []
    states = []
    for settings, paths in groups:
        draws = toolpath.schedule(paths, settings, origin)
        wanted.extend([(graphtec.su(x - ox), graphtec.su(y - oy)) for x, y in points]
                      for _, points in draws)
        if device.detects_tools:
            states.extend([verify._requested_motion_settings(settings, device)] * len(draws))
    decoded = verify.decode(stream)
    got = [[(graphtec.su(x), graphtec.su(y)) for x, y in path] for path in decoded.paths]
    if not wanted or got != wanted:
        raise ValueError("The test cut stream does not match the requested geometry.")
    if states and verify._observed_motion_settings(decoded.commands) != states:
        raise ValueError("The test cut stream does not match the settings for each sample.")
    width, height = map(graphtec.su, limit)
    if any(not (0 <= x <= width and 0 <= y <= height) for path in got for x, y in path):
        raise ValueError("A test cut path is outside the cut window. Check the mat and calibration.")


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


def settings_for_shape(base, shape):
    if shape in ("studio", "ring"):
        # Finish the inner contour while the outer contour still holds the material.
        return dataclasses.replace(base, path_order="design", pass_order="path",
                                   interior_first=False, delicate_media=False)
    return base


def build(device: graphtec.Device, base: CutSettings, forces=None,
          speeds=None, passes=None, depths=None,
          size_mm: float | None = None, gap_mm: float = DEFAULT_GAP_MM,
          margin_mm: float = DEFAULT_MARGIN_MM, cols: int = 0,
          sheet_w_mm: float = 215.9, sheet_h_mm: float = 279.4,
          registration: bool = False,
          offset_mm: tuple[float, float] = (0.0, 0.0),
          shape: str = "square", mat: str = "auto", end_position: str = "below",
          end_feed_mm: float = 0.0, spot: int | None = None, from_bottom: bool = False,
          spot_rect: Rect | None = None) -> TestSheet:
    """Lay out the test squares and build the job.

    An explicit spot places one settings combination at a numbered cell.
    Spots are numbered left to right, starting at one.
    """
    if shape not in TEST_SHAPES:
        raise ValueError(f"Unknown test cut shape {shape!r}.")
    if size_mm is None:
        size_mm = 10.0 if shape == "studio" else DEFAULT_SIZE_MM
    if (not all(math.isfinite(v) for v in (size_mm, gap_mm, margin_mm, sheet_w_mm, sheet_h_mm, *offset_mm))
            or size_mm <= 0 or gap_mm < 0 or margin_mm < 0):
        raise ValueError("Use finite settings, a positive test cut size, and a nonnegative gap and margin.")
    if sheet_w_mm > device.width_mm or sheet_h_mm > device.length_mm:
        raise ValueError(f"The test sheet is too large for {device.name}.")
    mat_errors = cutting_mat_errors(device, mat, sheet_w_mm, sheet_h_mm)
    if mat_errors:
        raise ValueError(mat_errors[0])
    requested = combinations(settings_for_shape(base, shape), forces, speeds, passes, depths)
    if any(setting.passes < 1 for setting in requested):
        raise ValueError("Set at least one cutting pass per sample.")
    settings = [setting.clamped(device) for setting in requested]
    if len({setting.depth is None for setting in settings}) > 1:
        raise ValueError("Test manual and automatic blade depth on separate sheets.")
    if not settings:
        raise ValueError("no settings to test")
    if len(settings) > MAX_CELLS:
        raise ValueError(
            f"{len(settings)} combinations is more than a sheet can usefully "
            f"carry (limit {MAX_CELLS}). Vary one axis at a time.")

    pitch = size_mm + gap_mm
    usable_w = sheet_w_mm - 2 * margin_mm
    usable_h = sheet_h_mm - 2 * margin_mm
    if size_mm > usable_w:
        raise ValueError("The test cut size and margins exceed the sheet width.")
    max_cols = max(1, int((usable_w + gap_mm) // pitch))
    cols = min(cols, max_cols) if cols > 0 else max_cols
    if spot is not None:
        if len(settings) != 1 or type(spot) is not int or spot < 1:
            raise ValueError("A numbered test spot requires one settings combination and a positive spot number.")
        first = spot - 1
    else:
        first = 0
    rows = -(-(first + len(settings)) // cols)
    if spot_rect is not None and (spot is None or spot_rect.w != size_mm or spot_rect.h != size_mm
            or not (margin_mm - 1e-9 <= spot_rect.x <= margin_mm + usable_w - size_mm + 1e-9
                    and margin_mm - 1e-9 <= spot_rect.y <= margin_mm + usable_h - size_mm + 1e-9)):
        raise ValueError("This sample lies outside the usable part of the test sheet.")
    if spot_rect is None and rows * pitch - gap_mm > usable_h:
        if spot is not None:
            raise ValueError("This spot is outside the test sheet. Start a new sheet or select larger stock.")
        raise ValueError(
            f"{len(settings)} squares of {size_mm:g} mm need "
            f"{rows * pitch - gap_mm:.0f} mm of height but only "
            f"{usable_h:.0f} mm is usable. Use fewer, or a smaller --size.")

    cells = []
    for i, s in enumerate(settings, first):
        row, col = divmod(i, cols)
        y = sheet_h_mm - margin_mm - size_mm - row * pitch if from_bottom else margin_mm + row * pitch
        cells.append(Cell(
            index=i, row=row, col=col,
            rect=spot_rect if spot_rect is not None else Rect(margin_mm + col * pitch, y, size_mm, size_mm),
            settings=s))

    origin = (margin_mm, margin_mm) if registration else (0.0, 0.0)
    width, height = ((usable_w, usable_h) if registration else (sheet_w_mm, sheet_h_mm))
    job = graphtec.CutJob(
        device, base, origin_mm=origin, reg_width_mm=width, reg_length_mm=height,
        registration=registration, offset_mm=offset_mm,
        sheet_size_mm=(sheet_w_mm, sheet_h_mm), mat=mat, end_position=end_position,
        end_feed_mm=end_feed_mm)
    groups = [(c.settings, _cell_paths(c.rect, shape)) for c in cells]
    steps = job.plan_matrix(groups)
    stream = b"".join(s.data for s in steps if s.kind == "write")
    plot_origin = tuple(axis - offset for axis, offset in zip(origin, offset_mm))
    _check_geometry(stream, groups, device, plot_origin, (width, height))

    return TestSheet(cells=tuple(cells), device=device, size_mm=size_mm,
                     registration=registration, stream=stream, steps=steps, shape=shape)


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
        if sheet.shape == "square":
            out.append(f'  <rect x="{n(r.x)}" y="{n(r.y)}" width="{n(r.w)}" '
                       f'height="{n(r.h)}" fill="none" stroke="#ff0000" '
                       f'stroke-width="0.2"/>')
        else:
            for sample in _cell_paths(r, sheet.shape):
                points = " ".join(f"{n(x)},{n(y)}" for x, y in sample.points)
                element = "polygon" if sample.closed else "polyline"
                out.append(f'  <{element} points="{points}" fill="none" stroke="#ff0000" '
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
