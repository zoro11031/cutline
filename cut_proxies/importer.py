"""Open an SVG or DXF and turn it into cut paths.

Until now this project could only cut grids it had generated itself, which
is the one thing Silhouette Studio does that mattered most and was missing:
open a file, cut what is in it. That covers Card Maker's own `.dxf`
templates, a sheet laid out by hand in Inkscape, a shape someone sent you,
and the templates this project writes.

Everything resolves to millimetres and then to flattened polylines, because
that is what the machine takes. Curves are subdivided to
`template.CHORD_TOLERANCE_MM`, which is half a Silhouette unit, so the
flattening error is below the smallest step the cutter can make.

**Units are the whole problem with SVG.** A file can declare `width="210mm"`
with `viewBox="0 0 744 1052"`, in which case one user unit is 210/744 mm;
or give no units at all, in which case the SVG spec says the user unit is a
CSS pixel at 96 dpi, and Illustrator's old files mean 72. Getting this
wrong does not fail - it cuts something the right shape and the wrong size,
which is the expensive kind of wrong. So the scale actually used is always
reported, and `cut-proxies import` prints it.

What is deliberately not supported: text (convert to paths first), clipping
paths, masks, patterns and CSS stylesheets. Each would be silently
mis-cut rather than refused if half-implemented, so they are detected and
reported instead.
"""

from __future__ import annotations

import dataclasses
from collections import defaultdict
import math
import re
import xml.etree.ElementTree as ET

from .geometry import Rect
from .template import CHORD_TOLERANCE_MM, CutPath, rounded_rect_points

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_HREF = "{http://www.w3.org/1999/xlink}href"
INKSCAPE_NS = "http://www.inkscape.org/namespaces/inkscape"
INKSCAPE_LABEL = f"{{{INKSCAPE_NS}}}label"
INKSCAPE_GROUPMODE = f"{{{INKSCAPE_NS}}}groupmode"

# IDs and geometry from inkscape-silhouette/render_silhouette_regmarks.py.
INKSCAPE_MARK_IDS = ("regmark-tl", "regmark-tr", "regmark-bl", "regmark-br")


@dataclasses.dataclass(frozen=True)
class InkscapeMarks:
    inset_mm: float
    pattern: str
    length_mm: float = 20.0
    thickness_mm: float = 0.3
    window_mm: tuple[float, float, float, float] | None = None

# CSS px per inch, which is what an SVG user unit means when nothing else
# says otherwise.
CSS_DPI = 96.0
MM_PER_IN = 25.4

_UNITS_MM = {
    "mm": 1.0,
    "cm": 10.0,
    "in": MM_PER_IN,
    "pt": MM_PER_IN / 72.0,
    "pc": MM_PER_IN / 6.0,
    "px": MM_PER_IN / CSS_DPI,
    "": MM_PER_IN / CSS_DPI,
}

_NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


class ImportError_(ValueError):
    """Raised rather than cutting something we only half understand."""


class InkscapePageRequired(ImportError_):
    def __init__(self, count: int):
        self.count = count
        super().__init__(f"Select an Inkscape page from 1 to {count} with --svg-page, "
                         "or export separate SVG files.")


@dataclasses.dataclass
class Imported:
    paths: tuple[CutPath, ...]
    width_mm: float
    height_mm: float
    source: str
    scale_note: str
    warnings: list[str] = dataclasses.field(default_factory=list)

    # Explicit SVG viewport; DXF extents are artwork bounds, not a page.
    page_size_mm: tuple[float, float] | None = None
    inkscape_marks: InkscapeMarks | None = None
    inkscape_mark_error: str | None = None

    def colours(self) -> dict[str, int]:
        """Stroke colours present, and how many paths each has."""
        out: dict[str, int] = {}
        for p in self.paths:
            c = p.stroke
            out[c or "(none)"] = out.get(c or "(none)", 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def only(self, colour: str) -> "Imported":
        """The same drawing with only the paths of one stroke colour.

        This is Studio's cut-by-colour, and it is what makes a file that
        carries both cut lines and printed guides usable: cut the red,
        leave the black alone.
        """
        want = '' if colour == '(none)' else normalise_colour(colour)
        kept = tuple(p for p in self.paths if p.stroke == want)
        if not kept:
            raise ImportError_(
                f"no paths with stroke {colour!r}; this file has "
                + ", ".join(f"{c} ({n})" for c, n in self.colours().items()))
        return dataclasses.replace(self, paths=kept)

    @property
    def bounds(self):
        xs = [x for p in self.paths for x, _ in p.points]
        ys = [y for p in self.paths for _, y in p.points]
        if not xs:
            return (0.0, 0.0, 0.0, 0.0)
        return (min(xs), min(ys), max(xs), max(ys))

    def describe(self) -> str:
        x0, y0, x1, y1 = self.bounds
        return (f"{len(self.paths)} path(s), "
                f"{x1 - x0:.1f} x {y1 - y0:.1f} mm "
                f"at ({x0:.1f}, {y0:.1f}); {self.scale_note}")


# --------------------------------------------------------------------------
# lengths and transforms
# --------------------------------------------------------------------------

def length_mm(value: str | None, default: float | None = None) -> float | None:
    """An SVG length to millimetres, or None if absent."""
    if value is None:
        return default
    value = value.strip()
    m = re.fullmatch(r"([-+0-9.eE]+)\s*([a-z%]*)", value)
    if not m:
        return default
    try:
        number = float(m.group(1))
    except ValueError:
        return default
    unit = m.group(2)
    if unit == "%":
        return default
    if unit not in _UNITS_MM:
        return default
    return number * _UNITS_MM[unit]


def _matmul(a, b):
    """Compose two (a, b, c, d, e, f) affine transforms: a then b."""
    a0, a1, a2, a3, a4, a5 = a
    b0, b1, b2, b3, b4, b5 = b
    return (a0 * b0 + a2 * b1, a1 * b0 + a3 * b1,
            a0 * b2 + a2 * b3, a1 * b2 + a3 * b3,
            a0 * b4 + a2 * b5 + a4, a1 * b4 + a3 * b5 + a5)


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def parse_transform(text: str | None):
    """An SVG `transform` attribute to an affine tuple."""
    if not text:
        return IDENTITY
    out = IDENTITY
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", text):
        nums = [float(n) for n in _NUM.findall(args)]
        if name == "translate":
            tx = nums[0] if nums else 0.0
            ty = nums[1] if len(nums) > 1 else 0.0
            m = (1, 0, 0, 1, tx, ty)
        elif name == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            m = (sx, 0, 0, sy, 0, 0)
        elif name == "rotate":
            a = math.radians(nums[0] if nums else 0.0)
            cos, sin = math.cos(a), math.sin(a)
            m = (cos, sin, -sin, cos, 0, 0)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                m = _matmul(_matmul((1, 0, 0, 1, cx, cy), m),
                            (1, 0, 0, 1, -cx, -cy))
        elif name == "matrix" and len(nums) >= 6:
            m = tuple(nums[:6])
        elif name == "skewX":
            m = (1, 0, math.tan(math.radians(nums[0])), 1, 0, 0)
        elif name == "skewY":
            m = (1, math.tan(math.radians(nums[0])), 0, 1, 0, 0)
        else:
            continue
        out = _matmul(out, m)
    return out


def apply(mat, x, y):
    a, b, c, d, e, f = mat
    return (a * x + c * y + e, b * x + d * y + f)


# --------------------------------------------------------------------------
# curve flattening
# --------------------------------------------------------------------------

def _steps_for(points, tolerance):
    """Segment count for a curve, from its control polygon length."""
    span = sum(math.dist(points[i], points[i + 1])
               for i in range(len(points) - 1))
    if span <= 0:
        return 1
    return max(2, min(200, int(math.ceil(span / max(tolerance * 8, 1e-6)))))


def _cubic(p0, p1, p2, p3, tolerance):
    n = _steps_for([p0, p1, p2, p3], tolerance)
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((
            u * u * u * p0[0] + 3 * u * u * t * p1[0]
            + 3 * u * t * t * p2[0] + t * t * t * p3[0],
            u * u * u * p0[1] + 3 * u * u * t * p1[1]
            + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def _quadratic(p0, p1, p2, tolerance):
    n = _steps_for([p0, p1, p2], tolerance)
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
                    u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1]))
    return out


def _arc(p0, rx, ry, rotation, large, sweep, p1, tolerance):
    """SVG elliptical arc, by the implementation notes in the spec."""
    if rx == 0 or ry == 0 or p0 == p1:
        return [p1]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(rotation)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx2, dy2 = (p0[0] - p1[0]) / 2.0, (p0[1] - p1[1]) / 2.0
    x1 = cos_p * dx2 + sin_p * dy2
    y1 = -sin_p * dx2 + cos_p * dy2
    # Scale the radii up if they cannot span the chord.
    lam = (x1 * x1) / (rx * rx) + (y1 * y1) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    denom = rx * rx * y1 * y1 + ry * ry * x1 * x1
    num = max(0.0, rx * rx * ry * ry - denom)
    coef = (0 if denom == 0 else math.sqrt(num / denom))
    if large == sweep:
        coef = -coef
    cx1 = coef * rx * y1 / ry
    cy1 = -coef * ry * x1 / rx
    cx = cos_p * cx1 - sin_p * cy1 + (p0[0] + p1[0]) / 2.0
    cy = sin_p * cx1 + cos_p * cy1 + (p0[1] + p1[1]) / 2.0

    def angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        det = ux * vy - uy * vx
        return math.atan2(det, dot)

    theta0 = angle(1, 0, (x1 - cx1) / rx, (y1 - cy1) / ry)
    delta = angle((x1 - cx1) / rx, (y1 - cy1) / ry,
                  (-x1 - cx1) / rx, (-y1 - cy1) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi

    n = max(2, min(400, int(math.ceil(
        abs(delta) / (2 * math.acos(max(-1.0, min(1.0,
            1 - tolerance / max(rx, ry))))) if max(rx, ry) > tolerance
        else 8))))
    out = []
    for i in range(1, n + 1):
        th = theta0 + delta * (i / n)
        ex, ey = rx * math.cos(th), ry * math.sin(th)
        out.append((cos_p * ex - sin_p * ey + cx,
                    sin_p * ex + cos_p * ey + cy))
    return out


# --------------------------------------------------------------------------
# SVG path data
# --------------------------------------------------------------------------

_CMD = re.compile(r"([MmZzLlHhVvCcSsQqTtAa])|([-+]?(?:\d*\.\d+|\d+\.?)"
                  r"(?:[eE][-+]?\d+)?)")


def parse_path_d(d: str, tolerance: float = CHORD_TOLERANCE_MM):
    """SVG path data to a list of (points, closed) subpaths in user units."""
    tokens = []
    for cmd, num in _CMD.findall(d or ""):
        tokens.append(cmd if cmd else float(num))

    subpaths = []
    pts: list[tuple[float, float]] = []
    closed = False
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    prev_cubic = None
    prev_quad = None
    op = None
    i = 0

    def flush():
        nonlocal pts, closed
        if len(pts) > 1:
            subpaths.append((pts, closed))
        pts = []
        closed = False

    def take(n):
        nonlocal i
        vals = tokens[i:i + n]
        i += n
        if len(vals) < n or any(isinstance(v, str) for v in vals):
            raise ImportError_(f"malformed path data near token {i}")
        return vals

    while i < len(tokens):
        tok = tokens[i]
        if isinstance(tok, str):
            op = tok
            i += 1
            if op in "Zz":
                if pts:
                    closed = True
                    flush()
                cur = start
                continue
        elif op is None:
            raise ImportError_("path data does not start with a command")
        elif op in "Mm":
            op = "L" if op == "M" else "l"
        rel = op.islower()
        up = op.upper()

        if up == "M":
            x, y = take(2)
            cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
            flush()
            pts = [cur]
            start = cur
            prev_cubic = prev_quad = None
        elif up == "L":
            x, y = take(2)
            cur = (cur[0] + x, cur[1] + y) if rel else (x, y)
            pts.append(cur)
            prev_cubic = prev_quad = None
        elif up == "H":
            (x,) = take(1)
            cur = (cur[0] + x, cur[1]) if rel else (x, cur[1])
            pts.append(cur)
            prev_cubic = prev_quad = None
        elif up == "V":
            (y,) = take(1)
            cur = (cur[0], cur[1] + y) if rel else (cur[0], y)
            pts.append(cur)
            prev_cubic = prev_quad = None
        elif up == "C":
            x1, y1, x2, y2, x, y = take(6)
            if rel:
                x1, y1 = cur[0] + x1, cur[1] + y1
                x2, y2 = cur[0] + x2, cur[1] + y2
                x, y = cur[0] + x, cur[1] + y
            pts += _cubic(cur, (x1, y1), (x2, y2), (x, y), tolerance)
            prev_cubic = (x2, y2)
            prev_quad = None
            cur = (x, y)
        elif up == "S":
            x2, y2, x, y = take(4)
            if rel:
                x2, y2 = cur[0] + x2, cur[1] + y2
                x, y = cur[0] + x, cur[1] + y
            c1 = (2 * cur[0] - prev_cubic[0], 2 * cur[1] - prev_cubic[1]) \
                if prev_cubic else cur
            pts += _cubic(cur, c1, (x2, y2), (x, y), tolerance)
            prev_cubic = (x2, y2)
            prev_quad = None
            cur = (x, y)
        elif up == "Q":
            x1, y1, x, y = take(4)
            if rel:
                x1, y1 = cur[0] + x1, cur[1] + y1
                x, y = cur[0] + x, cur[1] + y
            pts += _quadratic(cur, (x1, y1), (x, y), tolerance)
            prev_quad = (x1, y1)
            prev_cubic = None
            cur = (x, y)
        elif up == "T":
            x, y = take(2)
            if rel:
                x, y = cur[0] + x, cur[1] + y
            c = (2 * cur[0] - prev_quad[0], 2 * cur[1] - prev_quad[1]) \
                if prev_quad else cur
            pts += _quadratic(cur, c, (x, y), tolerance)
            prev_quad = c
            prev_cubic = None
            cur = (x, y)
        elif up == "A":
            rx, ry, rot, large, sweep, x, y = take(7)
            if rel:
                x, y = cur[0] + x, cur[1] + y
            pts += _arc(cur, rx, ry, rot, bool(large), bool(sweep), (x, y),
                        tolerance)
            cur = (x, y)
            prev_cubic = prev_quad = None
        else:
            raise ImportError_(f"unsupported path command {op!r}")
        if not pts:
            pts = [cur]
    flush()
    return subpaths


# --------------------------------------------------------------------------
# SVG document
# --------------------------------------------------------------------------

def _tag(el) -> str:
    return el.tag.split("}")[-1] if "}" in el.tag else el.tag


_NAMED = {"black": "#000000", "red": "#ff0000", "green": "#008000",
          "blue": "#0000ff", "white": "#ffffff", "none": "none",
          "yellow": "#ffff00", "lime": "#00ff00", "cyan": "#00ffff",
          "aqua": "#00ffff", "magenta": "#ff00ff", "fuchsia": "#ff00ff"}


def normalise_colour(value: str | None) -> str:
    """A stroke value to a comparable lower-case hex string.

    Silhouette Studio selects what to cut by line colour, which is how a
    single file carries both cut lines and print-only guides. Matching it
    needs colours that compare equal regardless of spelling: `#F00`,
    `#ff0000` and `red` are one colour.
    """
    if not value:
        return ""
    v = value.strip().lower()
    v = _NAMED.get(v, v)
    if v.startswith("#"):
        h = v[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        if len(h) == 6:
            return "#" + h
    m = re.fullmatch(r"rgb\(\s*(\d+)\D+(\d+)\D+(\d+)\s*\)", v)
    if m:
        return "#%02x%02x%02x" % tuple(int(g) for g in m.groups())
    return v


def _shape_subpaths(el, tolerance):
    """A shape element to subpaths in user units."""
    name = _tag(el)
    g = lambda k, d=0.0: float(el.get(k, d))      # noqa: E731
    if name == "path":
        return parse_path_d(el.get("d", ""), tolerance)
    if name == "rect":
        x, y, w, h = g("x"), g("y"), g("width"), g("height")
        rx = el.get("rx")
        ry = el.get("ry")
        raw_rx = float(rx if rx is not None else (ry or 0))
        raw_ry = float(ry if ry is not None else raw_rx)
        rx, ry = max(0.0, min(raw_rx, w / 2)), max(0.0, min(raw_ry, h / 2))
        if rx <= 0 or ry <= 0:
            return [([(x, y), (x + w, y), (x + w, y + h), (x, y + h)], True)]
        d = (f"M{x + rx},{y} H{x + w - rx} A{rx},{ry} 0 0 1 {x + w},{y + ry} "
             f"V{y + h - ry} A{rx},{ry} 0 0 1 {x + w - rx},{y + h} "
             f"H{x + rx} A{rx},{ry} 0 0 1 {x},{y + h - ry} "
             f"V{y + ry} A{rx},{ry} 0 0 1 {x + rx},{y} Z")
        return parse_path_d(d, tolerance)
    if name == "line":
        return [([(g("x1"), g("y1")), (g("x2"), g("y2"))], False)]
    if name in ("polyline", "polygon"):
        nums = [float(n) for n in _NUM.findall(el.get("points", ""))]
        pts = list(zip(nums[0::2], nums[1::2]))
        if len(pts) < 2:
            return []
        return [(pts, name == "polygon")]
    if name in ("circle", "ellipse"):
        cx, cy = g("cx"), g("cy")
        if name == "circle":
            rx = ry = g("r")
        else:
            rx, ry = g("rx"), g("ry")
        if rx <= 0 or ry <= 0:
            return []
        d = (f"M{cx - rx},{cy} A{rx},{ry} 0 1 0 {cx + rx},{cy} "
             f"A{rx},{ry} 0 1 0 {cx - rx},{cy} Z")
        return parse_path_d(d, tolerance)
    return []


UNSUPPORTED = {"text", "tspan", "image", "clipPath", "mask", "pattern",
               "foreignObject"}


def _inkscape_marks(mark_paths, page_size):
    """Measure the rendered corners used by render_silhouette_regmarks.py."""
    if page_size is None:
        raise ImportError_("Inkscape registration requires an explicit SVG page size.")
    width, height = page_size
    corners = len(INKSCAPE_MARK_IDS) if "regmark-br" in mark_paths else 3
    four = corners == 4
    length = 20
    marks = [mark_paths.get(marker, ((), False)) for marker in INKSCAPE_MARK_IDS[:corners]]
    for index, (points, closed) in enumerate(marks):
        square = index == 0 and not four
        if len(points) != (4 if square else 3) or closed != square:
            raise ImportError_("Inkscape registration requires a complete set of "
                               "standard square and L-shaped marks.")
    tl, tr, bl = (points for points, _ in marks[:3])
    x = tl[1][0] if four else min(p[0] for p in tl)
    y = tl[1][1] if four else min(p[1] for p in tl)
    right = tr[1][0]
    bottom = bl[1][1]
    if not (0 < x < right < width and 0 < y < bottom < height
            and min(right - x, bottom - y) >= 2 * length):
        raise ImportError_("Inkscape registration marks do not fit the page.")
    specs = [(x, y, 1, 1), (right, y, -1, 1), (x, bottom, 1, -1),
             (right, bottom, -1, -1)]
    for index, ((points, _), (cx, cy, dx, dy)) in enumerate(zip(marks, specs)):
        if index == 0 and not four:
            expected = [(x, y), (x + 5, y), (x + 5, y + 5), (x, y + 5)]
            matches = all(any(math.dist(p, q) <= 0.05 for q in points) for p in expected)
        else:
            expected = [(cx + dx * length, cy), (cx, cy), (cx, cy + dy * length)]
            matches = (all(math.dist(p, q) <= 0.05 for p, q in zip(points, expected))
                       or all(math.dist(p, q) <= 0.05 for p, q in zip(reversed(points), expected)))
        if not matches:
            raise ImportError_("Inkscape registration marks must match the plugin's "
                               "standard geometry and align in rows and columns.")
    window = tuple(round(v, 6) for v in (x, y, right, bottom))
    return InkscapeMarks(window[0], "four" if four else "three", window_mm=window)


def _layer_captions(root):
    """Disambiguate identically named layers under the same parent."""
    siblings = defaultdict(list)

    def collect(element, parent=None):
        label = element.get(INKSCAPE_LABEL)
        if _tag(element) == "g" and (label or element.get(INKSCAPE_GROUPMODE) == "layer"):
            name = label or element.get("id", "Layer")
            siblings[parent, name].append(element)
            parent = element
        for child in element:
            collect(child, parent)

    collect(root)
    captions = {}
    for (_parent, name), layers in siblings.items():
        for index, element in enumerate(layers, 1):
            distinct = len(layers) > 1 or name == "(no layer)"
            captions[element] = f"{name} [{element.get('id') or index}]" if distinct else name
    return captions


def _clone_translation(element):
    """SVG use x/y in local user units, before the instance transform."""
    values = []
    for attribute in ("x", "y"):
        raw = element.get(attribute, "0").strip()
        match = re.fullmatch(r"(" + _NUM.pattern + r")([a-z]*)", raw, re.I)
        if not match or match[2].lower() not in _UNITS_MM:
            raise ImportError_(f"Unsupported SVG clone translation {attribute}={raw!r}.")
        values.append(float(match[1]) * (_UNITS_MM[match[2].lower()] / _UNITS_MM["px"]))
    return (1, 0, 0, 1, *values)


def load_svg(path, tolerance: float = CHORD_TOLERANCE_MM, *,
             page: int | None = None) -> Imported:
    """Read an SVG into cut paths in millimetres."""
    try:
        tree = ET.parse(path if hasattr(path, 'read') else str(path))
    except ET.ParseError as exc:
        raise ImportError_(f"not a readable SVG: {exc}") from None
    root = tree.getroot()
    if _tag(root) != "svg":
        raise ImportError_("the root element is not <svg>")

    pages = root.findall(f".//{{{INKSCAPE_NS}}}page")
    if len(pages) > 1 and page is None:
        raise InkscapePageRequired(len(pages))
    if page is not None and not 1 <= page <= max(len(pages), 1):
        raise ImportError_(f"Inkscape page {page} does not exist in this SVG.")

    vb = root.get("viewBox")
    vb_nums = [float(n) for n in _NUM.findall(vb)] if vb else None
    w_mm = length_mm(root.get("width"))
    h_mm = length_mm(root.get("height"))

    page_size = (w_mm, h_mm) if w_mm and h_mm else None

    # Decide what one user unit is worth, and be able to say why.
    if vb_nums and len(vb_nums) == 4 and vb_nums[2] and vb_nums[3]:
        vx, vy, vw, vh = vb_nums
        if w_mm and h_mm:
            sx, sy = w_mm / vw, h_mm / vh
            note = (f"viewBox {vw:g}x{vh:g} mapped to {w_mm:g}x{h_mm:g} mm "
                    f"({sx:.4f} mm per unit)")
        else:
            sx = sy = _UNITS_MM["px"]
            w_mm, h_mm = vw * sx, vh * sy
            note = (f"no width/height: viewBox units taken as CSS px at "
                    f"{CSS_DPI:g} dpi")
        pre = (1, 0, 0, 1, -vx, -vy)
    else:
        sx = sy = _UNITS_MM["px"]
        # Physical viewport dimensions do not redefine user units.
        # https://www.w3.org/TR/SVG11/coords.html#InitialCoordinateSystem
        w_mm = w_mm or 0.0
        h_mm = h_mm or 0.0
        note = f"no viewBox: units taken as CSS px at {CSS_DPI:g} dpi"
        pre = IDENTITY

    tx = ty = 0.0
    if vb_nums and len(vb_nums) == 4 and vb_nums[2] and vb_nums[3]:
        # SVG 2 section 8.2: default xMidYMid meet, not a stretched viewBox.
        # https://www.w3.org/TR/SVG/coords.html#ComputingAViewportsTransform
        aspect = (root.get("preserveAspectRatio") or "xMidYMid meet").split()
        if aspect and aspect[0] == "defer":
            aspect = aspect[1:]
        if aspect and aspect[0] == "none":
            if len(aspect) > 1 and aspect[1:] not in (["meet"], ["slice"]):
                raise ImportError_("invalid preserveAspectRatio")
        else:
            if (not aspect or not re.fullmatch(r"x(Min|Mid|Max)Y(Min|Mid|Max)", aspect[0])
                    or len(aspect) > 2 or (len(aspect) == 2 and aspect[1] != "meet")):
                raise ImportError_("unsupported preserveAspectRatio; use meet or none, and flatten clipped geometry")
            align = re.fullmatch(r"x(Min|Mid|Max)Y(Min|Mid|Max)", aspect[0])
            scale = min(sx, sy)
            fraction = {"Min": 0.0, "Mid": 0.5, "Max": 1.0}
            tx = (w_mm - vw * scale) * fraction[align[1]]
            ty = (h_mm - vh * scale) * fraction[align[2]]
            sx = sy = scale
        note += f"; preserveAspectRatio {' '.join(aspect)}"
    base = _matmul((sx, 0, 0, sy, tx, ty), pre)
    selected_bounds = None
    if len(pages) > 1:
        selected = pages[page - 1]
        try:
            x, y, width, height = (float(selected.get(key, "0"))
                                   for key in ("x", "y", "width", "height"))
        except ValueError:
            raise ImportError_(f"Inkscape page {page} has invalid coordinates.") from None
        if not all(math.isfinite(v) for v in (x, y, width, height)) or min(width, height) <= 0:
            raise ImportError_(f"Inkscape page {page} has invalid bounds.")
        left, top = apply(base, x, y)
        w_mm, h_mm = width * sx, height * sy
        page_size = (w_mm, h_mm)
        selected_bounds = Rect(0, 0, w_mm, h_mm)
        base = _matmul((1, 0, 0, 1, -left, -top), base)
        note += f"; Inkscape page {page} of {len(pages)}"

    paths: list[CutPath] = []
    warnings: list[str] = []
    seen_unsupported: set[str] = set()
    elements_by_id = {}
    duplicate_ids = set()
    for element in root.iter():
        identifier = element.get("id")
        if identifier:
            if identifier in elements_by_id:
                duplicate_ids.add(identifier)
            else:
                elements_by_id[identifier] = element

    mark_paths = {}
    has_inkscape_marks = False
    ignored_layers = []
    captions = _layer_captions(root)

    def walk(el, mat, stroke, fill="#000000", layer=(), visibility="visible",
             in_marks=False, instance=(), color="#000000"):
        nonlocal has_inkscape_marks
        name = _tag(el)
        if name in {"defs", "metadata", "namedview", "symbol", "style"}:
            return
        label = el.get(INKSCAPE_LABEL, "") if name == "g" else ""
        marker_group = name == "g" and (el.get("id") == "regmark"
                                        or "regmark" in label.lower())
        if not in_marks and (marker_group or any(
                word in label.lower() for word in ("print", "cuttingmat"))):
            ignored_layers.append(label or el.get("id"))
            if not marker_group:
                return
            has_inkscape_marks = in_marks = True

        # The reference sender skips display:none and zero-opacity subtrees,
        # then handles inherited visibility when collecting geometry.
        style = {key.strip(): re.sub(r"\s*!\s*important$", "", val.strip(), flags=re.I)
                 for key, val in (field.split(":", 1)
                                  for field in (el.get("style") or "").split(";") if ":" in field)}
        if style.get("display", el.get("display")) == "none":
            return
        opacity = style.get("opacity", el.get("opacity", "1")).removesuffix("%")
        if _NUM.fullmatch(opacity) and float(opacity) <= 0:
            return
        own_visibility = style.get("visibility", el.get("visibility"))
        if own_visibility in ("visible", "hidden", "collapse"):
            visibility = own_visibility

        if name == "svg" and el is not root:
            raise ImportError_("nested SVG viewports are unsupported; flatten the drawing before cutting")
        mat = _matmul(mat, parse_transform(el.get("transform")))
        own_stroke = style.get("stroke", el.get("stroke"))
        if own_stroke and own_stroke != "inherit":
            stroke = normalise_colour(own_stroke)
        own_fill = style.get("fill", el.get("fill"))
        if own_fill and own_fill != "inherit":
            fill = normalise_colour(own_fill)
        own_color = normalise_colour(style.get("color", el.get("color")))
        if own_color and own_color not in ("inherit", "currentcolor"):
            color = own_color
        if not instance and el in captions:
            layer += (captions[el],)
        if in_marks:
            marker = el.get("id", "")
            stem, suffix, index = marker.partition("-page-")
            if suffix and index.isdigit() and (selected_bounds is None or int(index) == page):
                marker = stem
            elif selected_bounds is not None:
                marker = ""
            if marker in INKSCAPE_MARK_IDS and visibility == "visible":
                parts = _shape_subpaths(el, tolerance)
                if len(parts) == 1 and marker not in mark_paths:
                    points, closed = parts[0]
                    mark_paths[marker] = tuple(apply(mat, x, y) for x, y in points), closed
                else:
                    mark_paths[marker] = (), False
            for child in el:
                walk(child, mat, stroke, fill, layer, visibility, in_marks=True, color=color)
            return
        if name == "use":
            # inkscape-silhouette recursively walks the referenced object
            # in the clone's frame. External resources and nested viewports
            # require a renderer and are rejected here.
            href = el.get("href") or el.get(XLINK_HREF, "")
            if not href.startswith("#") or len(href) < 2:
                raise ImportError_("An SVG clone requires a local #id reference.")
            ref = href[1:]
            target = elements_by_id.get(ref)
            if target is None or ref in duplicate_ids:
                raise ImportError_(f"SVG clone target {href} is missing or ambiguous.")
            if ref in instance:
                raise ImportError_(f"SVG clone target {href} forms a recursive reference.")
            if _tag(target) in ("symbol", "svg", "defs"):
                raise ImportError_(f"SVG clone target {href} contains a viewport or definition. Unlink it in Inkscape.")
            walk(target, _matmul(mat, _clone_translation(el)), stroke, fill, layer, visibility,
                 instance=(*instance, ref), color=color)
            return
        if name in UNSUPPORTED:
            if visibility == "visible":
                seen_unsupported.add(name)
            return
        # Largest singular value bounds error after every parent transform,
        # including unequal scales and shear. Flatten in final millimetres.
        a, b, c, d = mat[:4]
        norm2 = a*a + b*b + c*c + d*d
        stretch = math.sqrt((norm2 + math.sqrt(max(0.0, norm2*norm2 - 4*(a*d-b*c)**2))) / 2)
        subpaths = (_shape_subpaths(el, tolerance / max(stretch, 1e-9))
                    if visibility == "visible" else ())
        for sub, closed in subpaths:
            pts = tuple(apply(mat, x, y) for x, y in sub)
            if len(pts) > 1:
                if selected_bounds is not None:
                    x_min, y_min = map(min, zip(*pts))
                    x_max, y_max = map(max, zip(*pts))
                    fuzz = 1e-6
                    if (x_max < -fuzz or y_max < -fuzz
                            or x_min > selected_bounds.x2 + fuzz
                            or y_min > selected_bounds.y2 + fuzz):
                        continue
                    if (x_min < -fuzz or y_min < -fuzz
                            or x_max > selected_bounds.x2 + fuzz
                            or y_max > selected_bounds.y2 + fuzz):
                        raise ImportError_(f"A cut path crosses the edge of Inkscape page {page}. "
                                           "Split it at the page edge before importing.")
                    pts = tuple((min(w_mm, max(0, x)), min(h_mm, max(0, y))) for x, y in pts)
                # The colour rides in the label so the whole pipeline can
                # carry it without a second channel.
                line_color = color if stroke == "currentcolor" else stroke
                fill_color = color if fill == "currentcolor" else fill
                tag = f"{name}|{line_color}" if line_color else name
                paths.append(CutPath(pts, closed=closed, label=tag, fill=fill_color, layer=layer))
        for child in el:
            walk(child, mat, stroke, fill, layer, visibility, instance=instance, color=color)

    walk(root, base, "")
    inkscape_marks = None
    mark_error = None
    if ignored_layers:
        warnings.append("Excluded Inkscape layers: " + ", ".join(ignored_layers))
    if has_inkscape_marks:
        try:
            inkscape_marks = _inkscape_marks(mark_paths, page_size)
        except ImportError_ as exc:
            mark_error = str(exc)
            warnings.append(mark_error)
        else:
            warnings.append(
                f"Inkscape registration: {inkscape_marks.pattern} marks, "
                f"origin ({inkscape_marks.window_mm[0]:g}, "
                f"{inkscape_marks.window_mm[1]:g}) mm, 20 mm arms.")

    if seen_unsupported:
        warnings.append(
            f"ignored unsupported element(s): "
            f"{', '.join(sorted(seen_unsupported))}. Convert text to paths "
            f"and flatten clips before cutting.")
    if root.find(f".//{{{SVG_NS}}}style") is not None or root.get("class"):
        warnings.append(
            "the file uses CSS, which is not interpreted here; geometry is "
            "read from attributes only")
    if not paths:
        raise ImportError_("no cuttable geometry found in this SVG")

    return Imported(tuple(paths), w_mm or 0.0, h_mm or 0.0, str(path), note,
                    warnings, page_size_mm=page_size, inkscape_marks=inkscape_marks,
                    inkscape_mark_error=mark_error)


# --------------------------------------------------------------------------
# DXF
# --------------------------------------------------------------------------

def _dxf_pairs(text: str):
    lines = text.splitlines()
    for i in range(0, len(lines) - 1, 2):
        code = lines[i].strip()
        if not code.lstrip("-").isdigit():
            continue
        yield int(code), lines[i + 1].strip()


def _bulge_arc(p0, p1, bulge, tolerance):
    """A DXF bulge segment to points, excluding the start."""
    if not bulge:
        return [p1]
    chord = math.dist(p0, p1)
    if chord == 0:
        return [p1]
    theta = 4 * math.atan(bulge)
    radius = chord / (2 * math.sin(abs(theta) / 2))
    n = max(2, min(200, int(math.ceil(
        abs(theta) / (2 * math.acos(max(-1.0, min(1.0,
            1 - tolerance / radius))))))))
    # Centre: perpendicular offset from the chord midpoint.
    mx, my = (p0[0] + p1[0]) / 2, (p0[1] + p1[1]) / 2
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    h = radius * math.cos(theta / 2)
    cx = mx - h * dy / chord
    cy = my + h * dx / chord
    a0 = math.atan2(p0[1] - cy, p0[0] - cx)
    out = []
    for i in range(1, n + 1):
        a = a0 + theta * (i / n)
        out.append((cx + radius * math.cos(a), cy + radius * math.sin(a)))
    return out


def load_dxf(path, tolerance: float = CHORD_TOLERANCE_MM) -> Imported:
    """Read a DXF into cut paths.

    Handles LINE, POLYLINE/VERTEX (with bulges), LWPOLYLINE, ARC and CIRCLE
    — which is what Silhouette Card Maker's templates and this project's own
    exports contain. DXF has no reliable units, so `$INSUNITS` is honoured
    when present and millimetres assumed otherwise; the assumption is
    reported rather than hidden.
    """
    text = _read_text(path)
    pairs = list(_dxf_pairs(text))
    if not any(v == "ENTITIES" for _c, v in pairs):
        raise ImportError_("no ENTITIES section; not a DXF we can read")

    units = None
    for i, (code, value) in enumerate(pairs):
        if code == 9 and value == "$INSUNITS" and i + 1 < len(pairs):
            try:
                units = int(pairs[i + 1][1])
            except ValueError:
                units = None
            break
    scale, note = {1: (MM_PER_IN, "$INSUNITS says inches"),
                   4: (1.0, "$INSUNITS says millimetres"),
                   5: (10.0, "$INSUNITS says centimetres")}.get(
        units, (1.0, "no $INSUNITS; millimetres assumed"))

    # Walk the ENTITIES section.
    start = next(i for i, (c, v) in enumerate(pairs)
                 if c == 2 and v == "ENTITIES")
    paths: list[CutPath] = []
    warnings: list[str] = []
    entity = None
    data: dict[int, list[str]] = {}
    verts: list[tuple[float, float, float]] = []
    poly_closed = False
    in_polyline = False
    seen_other: set[str] = set()

    def num(code, default=0.0, which=0):
        vals = data.get(code)
        if not vals or which >= len(vals):
            return default
        try:
            return float(vals[which])
        except ValueError:
            return default

    def finish():
        nonlocal entity, data, verts, poly_closed, in_polyline
        first = len(paths)
        if entity == "LINE":
            paths.append(CutPath(
                ((num(10), num(20)), (num(11), num(21))), False, "LINE"))
        elif entity == "CIRCLE":
            cx, cy, r = num(10), num(20), num(40)
            if r > 0:
                n = max(8, min(400, int(math.ceil(
                    math.pi / math.acos(max(-1.0, min(1.0,
                        1 - tolerance / r)))))))
                paths.append(CutPath(tuple(
                    (cx + r * math.cos(2 * math.pi * i / n),
                     cy + r * math.sin(2 * math.pi * i / n))
                    for i in range(n)), True, "CIRCLE"))
        elif entity == "ARC":
            cx, cy, r = num(10), num(20), num(40)
            a0, a1 = math.radians(num(50)), math.radians(num(51))
            if a1 <= a0:
                a1 += 2 * math.pi
            if r > 0:
                n = max(2, min(400, int(math.ceil(
                    (a1 - a0) / (2 * math.acos(max(-1.0, min(1.0,
                        1 - tolerance / r))))))))
                paths.append(CutPath(tuple(
                    (cx + r * math.cos(a0 + (a1 - a0) * i / n),
                     cy + r * math.sin(a0 + (a1 - a0) * i / n))
                    for i in range(n + 1)), False, "ARC"))
        elif entity == "LWPOLYLINE":
            xs = [float(v) for v in data.get(10, [])]
            ys = [float(v) for v in data.get(20, [])]
            bulges = [float(v) for v in data.get(42, [])]
            closed = int(num(70)) & 1 == 1
            pts = list(zip(xs, ys))
            if len(pts) > 1:
                paths.append(CutPath(
                    tuple(_expand_bulges(pts, bulges, closed, tolerance)),
                    closed, "LWPOLYLINE"))
        elif entity == "POLYLINE" and verts:
            pts = [(x, y) for x, y, _b in verts]
            bulges = [b for _x, _y, b in verts]
            if len(pts) > 1:
                paths.append(CutPath(
                    tuple(_expand_bulges(pts, bulges, poly_closed,
                                         tolerance)),
                    poly_closed, "POLYLINE"))
        elif entity in ("SPLINE", "ELLIPSE", "TEXT", "MTEXT", "INSERT"):
            seen_other.add(entity)
        if len(paths) > first:
            layer = data.get(8, ["0"])[0]
            true_colour = num(420, -1)
            aci_value = num(62, 256)
            aci = int(aci_value) if math.isfinite(aci_value) else 256
            primary = {1: "#ff0000", 2: "#ffff00", 3: "#00ff00", 4: "#00ffff",
                       5: "#0000ff", 6: "#ff00ff"}
            colour = (f"#{int(true_colour):06x}" if 0 <= true_colour <= 0xffffff
                      else primary.get(aci, f"aci {aci}" if 0 < aci < 256 else ""))
            paths[first:] = [dataclasses.replace(
                path, layer=(layer,), label=f"{path.label}|{colour}" if colour else path.label)
                for path in paths[first:]]
        entity, data, verts, poly_closed, in_polyline = None, {}, [], False, False

    i = start
    while i < len(pairs):
        code, value = pairs[i]
        if code == 0:
            if value == "VERTEX" and in_polyline:
                # Collect the vertex that follows.
                vx = vy = vb = 0.0
                j = i + 1
                while j < len(pairs) and pairs[j][0] != 0:
                    c, v = pairs[j]
                    try:
                        if c == 10: vx = float(v)
                        elif c == 20: vy = float(v)
                        elif c == 42: vb = float(v)
                    except ValueError:
                        pass
                    j += 1
                verts.append((vx, vy, vb))
                i = j
                continue
            if value == "SEQEND":
                finish()
                i += 1
                continue
            if entity is not None:
                finish()
            if value == "ENDSEC":
                break
            entity = value
            data = {}
            if value == "POLYLINE":
                in_polyline = True
        elif entity is not None:
            data.setdefault(code, []).append(value)
            if code == 70 and entity == "POLYLINE":
                try:
                    poly_closed = int(float(value)) & 1 == 1
                except ValueError:
                    pass
        i += 1
    if entity is not None:
        finish()

    if seen_other:
        warnings.append(
            f"ignored entity type(s): {', '.join(sorted(seen_other))}. "
            f"Explode splines and blocks before exporting.")
    if not paths:
        raise ImportError_("no cuttable entities found in this DXF")

    # DXF counts y upward; everything here counts downward from the top.
    #
    # Flip about the PAGE, not about the geometry's own bounding box.
    # Flipping about the bounds moves the drawing to y=0 and silently
    # discards where it sat on the sheet - which for a registered cut is
    # the whole point. $EXTMAX carries the page top when the writer set it
    # (ours does); without it there is nothing better than the geometry,
    # and the caller is told.
    ys = [y for p in paths for _x, y in p.points]
    top = max(ys)
    for i, (code, value) in enumerate(pairs):
        if code == 9 and value == "$EXTMAX":
            for c2, v2 in pairs[i + 1:i + 6]:
                if c2 == 20:
                    try:
                        top = max(top, float(v2))
                    except ValueError:
                        pass
                    break
            break
    else:
        warnings.append(
            "no $EXTMAX in this DXF, so the page height is unknown and the "
            "drawing is positioned from its own extent; check the placement "
            "against the marks before cutting")
    flipped = tuple(
        dataclasses.replace(p, points=tuple((x * scale, (top - y) * scale) for x, y in p.points))
        for p in paths)
    xs = [x for p in flipped for x, _y in p.points]
    ys2 = [y for p in flipped for _x, y in p.points]
    return Imported(flipped, max(xs) - min(xs), max(ys2) - min(ys2),
                    str(path), note + "; y flipped to top-left origin",
                    warnings)


def _expand_bulges(pts, bulges, closed, tolerance):
    out = [pts[0]]
    pairs = list(zip(pts, pts[1:]))
    if closed:
        pairs.append((pts[-1], pts[0]))
    for idx, (a, b) in enumerate(pairs):
        bulge = bulges[idx] if idx < len(bulges) else 0.0
        out.extend(_bulge_arc(a, b, bulge, tolerance))
    if closed and len(out) > 1 and math.dist(out[0], out[-1]) < 1e-9:
        out.pop()
    return out


def _read_text(path) -> str:
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        return fh.read()


def load_studio3(path, tolerance: float = CHORD_TOLERANCE_MM) -> Imported:
    """Import the rounded-card outlines understood by the Studio template reader."""
    from pathlib import Path
    from . import studio3

    data = Path(path).read_bytes()
    try:
        cards = studio3.read_cards(data)
        page = studio3.page_size(data)
    except studio3.Studio3Error as exc:
        raise ImportError_(str(exc)) from exc
    paths = tuple(CutPath(rounded_rect_points(Rect(c.x, c.y, c.w, c.h), c.radius, tolerance),
                          closed=True, label=f"Card {i}")
                  for i, c in enumerate(cards, 1))
    return Imported(paths, *page, str(path), "Studio3 card coordinates in mm",
                    ["Studio3 import reads rounded card outlines. Match registration to the printed sheet."],
                    page_size_mm=page)


def load(path, tolerance: float = CHORD_TOLERANCE_MM, *,
         page: int | None = None) -> Imported:
    """Read an SVG, DXF or Studio3 template, chosen by extension."""
    name = str(path).lower()
    if name.endswith(".svg"):
        return load_svg(path, tolerance, page=page)
    if page is not None:
        raise ImportError_("Page selection requires an Inkscape SVG.")
    if name.endswith(".dxf"):
        return load_dxf(path, tolerance)
    if name.endswith(".studio3"):
        return load_studio3(path, tolerance)
    raise ImportError_(f"Unsupported cut file {path}. Expected SVG, DXF or Studio3.")
