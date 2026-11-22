"""PixScan: straighten a photo of a PixScan mat to true size.

A PixScan mat carries black bars 1.5 mm thick just outside its photo area,
broken by short gaps. The gaps form a pattern unique to each line, so a
photo of the mat says which mat it is and where every gap sits in the
picture. Mapping the gaps' known millimetre positions onto where they were
found gives the perspective of the photo, which is then undone.

**Where the numbers come from.** Every mat, line, gap and the bar thickness
was read out of Silhouette Studio 5.0.556.1 (`Studio.exe` sha256
4871EDB71FB28B02…), `PhotoScan_spec.!spec` and
`PhotoScan_spec.MARKER_THICKNESS_MM`. Which physical mat each key is, and the
frame offset on the cutting mat, are not yet known. Coordinates are Studio's: millimetres, x right, y
down, the photo area from (0, 0) to its size.

**What is not Studio's.** The detection and fitting below are this
project's own. Studio's own path (`ScanLine.walkPixels`, a lens profile,
`Unperspectivize`) is not reproduced, and no photograph of a real mat has
been tried. Which physical mat each key is has not been established, so a
mat is reported by its key and photo-area size.
"""

from __future__ import annotations

import base64
import dataclasses
import io
import math

from . import editor_model as model, sticker_assets

MARKER_THICKNESS_MM = 1.5   # PhotoScan_spec.MARKER_THICKNESS_MM
DETECT_EDGE_PX = 1400       # longest side the detector works at
MAX_RESIDUAL_MM = 0.5       # RMS fit error beyond which a scan is refused
MAX_POINT_ERROR_MM = 1.0    # any single gap edge further out refuses it too
MIN_LINES = 2               # non-parallel lines needed for a perspective fit


class PixScanError(ValueError):
    """The photo could not be matched to a PixScan mat closely enough to use."""


@dataclasses.dataclass(frozen=True)
class Line:
    """One marker bar: switch points `marks` (mm) along it from `start`."""

    name: str
    type: int
    start: tuple[float, float]
    end: tuple[float, float]
    marks: tuple[float, ...]

    @property
    def length(self) -> float:
        return math.dist(self.start, self.end)

    def at(self, distance: float) -> tuple[float, float]:
        (x1, y1), (x2, y2) = self.start, self.end
        t = distance / self.length
        return x1 + (x2 - x1) * t, y1 + (y2 - y1) * t

    def gaps(self) -> tuple[tuple[float, float], ...]:
        """Each gap as (from, to) distances along the line."""
        return tuple(zip(self.marks[1:-1:2], self.marks[2::2]))

    def bars(self) -> tuple[tuple[float, float], ...]:
        return tuple(zip(self.marks[0::2], self.marks[1::2]))


@dataclasses.dataclass(frozen=True)
class Mat:
    key: int
    values: tuple[float, float]  # set after the lines in Studio; meaning not established
    lines: tuple[Line, ...]

    @property
    def photo_lines(self) -> tuple[Line, ...]:
        return tuple(line for line in self.lines if line.name.startswith("photo_"))

    @property
    def photo_size(self) -> tuple[float, float]:
        top = next(line for line in self.lines if line.name == "photo_top")
        side = next(line for line in self.lines if line.name == "photo_right")
        return top.length, side.length

    def describe(self) -> str:
        width, height = self.photo_size
        return f"PixScan mat {self.key} ({width:g} × {height:g} mm photo area)"

    @property
    def edges(self) -> tuple["Edge", ...]:
        """Physical edges: the union of every line's bars along one bar, with
        the gaps as whatever lies between. Where two of Studio's lines meet
        end to end on one edge, the break between them is a gap like any other."""
        bars: dict[tuple[str, float], list] = {}
        names: dict[tuple[str, float], list] = {}
        for line in self.lines:
            (x1, y1), (x2, y2) = line.start, line.end
            key = ("x", x1) if x1 == x2 else ("y", y1)
            along = 1 if key[0] == "x" else 0
            for a, b in line.bars():
                bars.setdefault(key, []).append(tuple(sorted((line.at(a)[along], line.at(b)[along]))))
            names.setdefault(key, []).append(line.name)
        edges = []
        for key in sorted(bars):
            merged = []
            for lo, hi in sorted(bars[key]):
                if merged and lo <= merged[-1][1] + 1e-6:
                    merged[-1][1] = max(merged[-1][1], hi)
                else:
                    merged.append([lo, hi])
            gaps = tuple((round(a[1], 6), round(b[0], 6)) for a, b in zip(merged, merged[1:]))
            edges.append(Edge(key[0], key[1], gaps, tuple(names[key])))
        return tuple(edges)


@dataclasses.dataclass(frozen=True)
class Edge:
    """One bar as it lies on the mat: x = constant (axis "x") or y = constant."""

    axis: str
    constant: float
    gaps: tuple[tuple[float, float], ...]   # (from, to) along the bar, ascending
    names: tuple[str, ...]

    def point(self, along: float) -> tuple[float, float]:
        return (self.constant, along) if self.axis == "x" else (along, self.constant)


# PhotoScan_spec.!spec, in Studio's order; keys are Studio's.
MATS = {
    1: Mat(1, (212, 360), (
        Line("photo_left", 1, (213.5, 218), (213.5, 0), (0, 80, 83, 218)),
        Line("photo_top", 1, (212, -1.5), (0, -1.5),
             (0, 9.5, 11, 22, 23.5, 104.5, 107.5, 188.5, 190, 201, 202.5, 212)),
        Line("photo_right", 1, (-1.5, 0), (-1.5, 218), (0, 20, 21.5, 135, 138, 218)),
        Line("scan_bottom", 2, (153.5, 361.5), (0, 361.5), (0, 43, 52, 96.5, 99.5, 153.5)),
        Line("scan_left_bottom", 2, (-1.5, 360), (-1.5, 221), (0, 35.5, 37, 139)),
        Line("scan_left_top", 3, (-1.5, 0), (-1.5, 135), (0, 20, 21.5, 135)),
        Line("scan_top", 3, (104.5, -1.5), (0, -1.5), (0, 81, 82.5, 93.5, 95, 104.5)),
    )),
    2: Mat(2, (275, 311), (
        Line("photo_left", 1, (276.5, 215), (276.5, 0),
             (0, 58, 61, 119, 122, 192, 193.5, 204.5, 206, 215)),
        Line("photo_top", 1, (275, -1.5), (0, -1.5), (0, 10.5, 12, 26, 27.5, 133.5, 136.5, 275)),
        Line("photo_right", 1, (-1.5, 0), (-1.5, 215), (0, 93, 96, 151, 160, 215)),
        Line("scan_bottom", 2, (141.5, -1.5), (275, -1.5), (0, 106, 107.5, 121.5, 123, 133.5)),
        Line("scan_left_bottom", 2, (276.5, 0), (276.5, 154), (0, 9, 10.5, 21.5, 23, 93, 96, 154)),
        Line("scan_top", 3, (141.5, 312.5), (275, 312.5), (0, 106, 107.5, 133.5)),
        Line("scan_left_top", 3, (276.5, 311), (276.5, 157), (0, 9, 10.5, 21.5, 23, 93, 96, 154)),
    )),
    3: Mat(3, (314.5, 236), (
        Line("photo_left", 1, (316, 186), (316, 0),
             (0, 9, 10.5, 60, 63, 71, 72.5, 86, 89, 97, 98.5, 174, 175.5, 186)),
        Line("photo_top", 1, (314.5, -1.5), (0, -1.5),
             (0, 68, 69.5, 137, 138.5, 149, 152, 193, 196, 207, 208.5, 217, 218.5, 274.5, 277.5, 314.5)),
        Line("photo_right", 1, (-1.5, 0), (-1.5, 186),
             (0, 23.5, 25, 86, 89, 109.5, 115.5, 136, 139, 176.5, 178, 186)),
        Line("scan_bottom", 2, (-1.5, 186), (-1.5, 0),
             (0, 8, 9.5, 47, 50, 70.5, 76.5, 97, 100, 161, 162.5, 186)),
        Line("scan_left_bottom", 2, (0, -1.5), (118.5, -1.5), (0, 37, 40, 96, 97.5, 106, 107.5, 118.5)),
        Line("scan_left_top", 3, (314.5, -1.5), (165.5, -1.5), (0, 68, 69.5, 137, 138.5, 149)),
        Line("scan_top", 3, (316, 186), (316, 0),
             (0, 9, 10.5, 60, 63, 71, 72.5, 86, 89, 97, 98.5, 174, 175.5, 186)),
    )),
    4: Mat(4, (225, 165), (
        Line("photo_left", 1, (226.5, 123), (226.5, 0),
             (0, 11.5, 13, 41, 42.5, 51, 52.5, 112, 113.5, 123)),
        Line("photo_top", 1, (225, -1.5), (0, -1.5),
             (0, 9, 10.5, 71, 72.5, 81, 84, 118, 119.5, 128, 131, 212, 213.5, 225)),
        Line("photo_right", 1, (-1.5, 0), (-1.5, 123), (0, 70.5, 72, 80.5, 82, 112.5, 114, 123)),
        Line("scan_bottom", 2, (226.5, 123), (226.5, 0),
             (0, 11.5, 13, 41, 42.5, 51, 52.5, 112, 113.5, 123)),
        Line("scan_left_bottom", 2, (225, -1.5), (0, -1.5),
             (0, 9, 10.5, 71, 72.5, 81, 84, 118, 119.5, 128, 131, 212, 213.5, 225)),
    )),
}


# ---- projective geometry ---------------------------------------------------

def _solve(matrix, vector):
    """Gaussian elimination with partial pivoting; no numpy on this machine's path."""
    n = len(vector)
    a = [row[:] + [value] for row, value in zip(matrix, vector)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise PixScanError("The marker positions do not fix the photo's perspective.")
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r != col:
                f = a[r][col] / a[col][col]
                if f:
                    a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [a[i][n] / a[i][i] for i in range(n)]


def _normaliser(points):
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    spread = sum(math.dist(p, (cx, cy)) for p in points) / len(points) or 1
    s = math.sqrt(2) / spread
    return ((s, 0, -s * cx), (0, s, -s * cy), (0, 0, 1))


def _mul(a, b):
    return tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3))


def _inverse(m):
    (a, b, c), (d, e, f), (g, h, i) = m
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1e-15:
        raise PixScanError("The marker positions do not fix the photo's perspective.")
    return ((( e * i - f * h) / det, -(b * i - c * h) / det, ( b * f - c * e) / det),
            (-(d * i - f * g) / det, ( a * i - c * g) / det, -(a * f - c * d) / det),
            (( d * h - e * g) / det, -(a * h - b * g) / det, ( a * e - b * d) / det))


def apply(h, point):
    x, y = point
    w = h[2][0] * x + h[2][1] * y + h[2][2]
    return ((h[0][0] * x + h[0][1] * y + h[0][2]) / w, (h[1][0] * x + h[1][1] * y + h[1][2]) / w)


def homography(source, target):
    """The projective map taking each source point to its target (least squares)."""
    if len(source) < 4:
        raise PixScanError("Too few marker positions to fit the photo's perspective.")
    ns, nt = _normaliser(source), _normaliser(target)
    rows, values = [], []
    for (x, y), (u, v) in zip((apply(ns, p) for p in source), (apply(nt, p) for p in target)):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y]); values.append(u)
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y]); values.append(v)
    normal = [[sum(r[i] * r[j] for r in rows) for j in range(8)] for i in range(8)]
    rhs = [sum(r[i] * value for r, value in zip(rows, values)) for i in range(8)]
    a, b, c, d, e, f, g, h = _solve(normal, rhs)
    fitted = _mul(_mul(_inverse(nt), ((a, b, c), (d, e, f), (g, h, 1))), ns)
    scale = fitted[2][2]
    return tuple(tuple(value / scale for value in row) for row in fitted)


# ---- finding the bars -------------------------------------------------------

@dataclasses.dataclass
class _Segment:
    a: tuple[float, float]      # end points in detection pixels
    b: tuple[float, float]
    width: float

    @property
    def centre(self):
        return (self.a[0] + self.b[0]) / 2, (self.a[1] + self.b[1]) / 2

    @property
    def direction(self):
        length = math.dist(self.a, self.b) or 1
        return (self.b[0] - self.a[0]) / length, (self.b[1] - self.a[1]) / length

    @property
    def length(self):
        return math.dist(self.a, self.b)


def _dark_mask(gray):
    """Pixels clearly darker than their neighbourhood: bars under uneven light."""
    from PIL import ImageChops, ImageFilter

    radius = max(4, round(max(gray.size) / 60))
    local = gray.filter(ImageFilter.BoxBlur(radius))
    difference = ImageChops.subtract(local, gray)
    return difference.point(lambda v: 255 if v > 28 else 0)


def _segments(mask):
    """Long thin dark components, each reduced to its centre line."""
    width, height = mask.size
    data = bytearray(mask.tobytes())
    out = []
    for start in range(len(data)):
        if data[start] != 255:
            continue
        data[start] = 0
        stack, pixels = [start], []
        while stack:
            index = stack.pop()
            pixels.append(index)
            x, y = index % width, index // width
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < width and 0 <= ny < height:
                    other = ny * width + nx
                    if data[other] == 255:
                        data[other] = 0
                        stack.append(other)
            if len(pixels) > 60000:
                break
        if not 6 <= len(pixels) <= 60000:
            continue
        xs = [p % width + 0.5 for p in pixels]
        ys = [p // width + 0.5 for p in pixels]
        n = len(pixels)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs) / n
        syy = sum((y - my) ** 2 for y in ys) / n
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
        angle = 0.5 * math.atan2(2 * sxy, sxx - syy)
        ux, uy = math.cos(angle), math.sin(angle)
        along = [(x - mx) * ux + (y - my) * uy for x, y in zip(xs, ys)]
        across = [-(x - mx) * uy + (y - my) * ux for x, y in zip(xs, ys)]
        length = max(along) - min(along) + 1
        thickness = max(across) - min(across) + 1
        if length < 4 * thickness or thickness > 25:
            continue
        # Pixel centres are half a pixel inside the bar's true ends.
        lo, hi = min(along) - 0.5, max(along) + 0.5
        out.append(_Segment((mx + ux * lo, my + uy * lo), (mx + ux * hi, my + uy * hi), thickness))
    return out


def _collinear(segment, line_point, line_direction, tolerance):
    ux, uy = line_direction
    sx, sy = segment.direction
    if abs(ux * sy - uy * sx) > 0.06:
        return False
    for px, py in (segment.a, segment.b):
        if abs(-(px - line_point[0]) * uy + (py - line_point[1]) * ux) > tolerance:
            return False
    return True


def _group(segments):
    """Segments lying on one straight line, longest first."""
    remaining = sorted(segments, key=lambda s: -s.length)
    lines = []
    while remaining:
        seed = remaining.pop(0)
        tolerance = max(2.0, seed.width * 1.2)
        members = [seed]
        rest = []
        for segment in remaining:
            (members if _collinear(segment, seed.centre, seed.direction, tolerance) else rest).append(segment)
        remaining = rest
        if len(members) >= 2:
            lines.append(members)
    return lines


def _gap_edges(members):
    """Detected gaps on one line as pairs of end points, in order along it."""
    ux, uy = members[0].direction
    origin = members[0].centre
    def t(point):
        return (point[0] - origin[0]) * ux + (point[1] - origin[1]) * uy
    ordered = sorted(((min(t(s.a), t(s.b)), s) for s in members), key=lambda item: item[0])
    pieces = []
    for _, s in ordered:
        a, b = (s.a, s.b) if t(s.a) <= t(s.b) else (s.b, s.a)
        pieces.append((a, b))
    gaps = []
    for (_, end), (start, _) in zip(pieces, pieces[1:]):
        gaps.append((end, start))
    return gaps


# ---- identifying lines ------------------------------------------------------

def _line_fit(pixels, mm, sides):
    """Fit m = (a t + b) / (c t + 1) along one bar, with every gap edge moved
    by `w` into the gap (`sides` +1 at a gap's start, -1 at its end): blur
    and exposure make bars look wider than they are, the same on every gap.
    Returns (RMS error in mm, (a, b, c, w))."""
    if len(pixels) < 5:
        return math.inf, None
    rows = [[t, 1, -t * m, -side] for t, m, side in zip(pixels, mm, sides)]
    normal = [[sum(r[i] * r[j] for r in rows) for j in range(4)] for i in range(4)]
    rhs = [sum(r[i] * m for r, m in zip(rows, mm)) for i in range(4)]
    try:
        a, b, c, w = _solve(normal, rhs)
    except PixScanError:
        return math.inf, None
    errors = [a * t + b - c * t * m - w * side - m for t, m, side in zip(pixels, mm, sides)]
    return math.sqrt(sum(e * e for e in errors) / len(errors)), (a, b, c, w)


def _candidates(gaps, mat):
    """Every way a detected run of gaps fits a run of gaps on one of `mat`'s edges.

    Each answer is (error, edge, [(mat point, photo point), ...]); the mat
    points are moved by the fitted widening, so they are where the photo's
    gap edges really are on the mat."""
    if len(gaps) < 3:
        return []
    origin = gaps[0][0]
    far = gaps[-1][1]
    length = math.dist(origin, far) or 1
    ux, uy = (far[0] - origin[0]) / length, (far[1] - origin[1]) / length
    along = [((a[0] - origin[0]) * ux + (a[1] - origin[1]) * uy,
              (b[0] - origin[0]) * ux + (b[1] - origin[1]) * uy) for a, b in gaps]
    found = []
    for edge in mat.edges:
        for reverse in (False, True):
            ordered = list(reversed(edge.gaps)) if reverse else list(edge.gaps)
            for offset in range(len(ordered) - len(gaps) + 1):
                run = ordered[offset:offset + len(gaps)]
                pixels, mm, sides = [], [], []
                for (ta, tb), (lo, hi) in zip(along, run):
                    first, second = (hi, lo) if reverse else (lo, hi)
                    pixels += [ta, tb]; mm += [first, second]
                    sides += [-1, 1] if reverse else [1, -1]
                error, fitted = _line_fit(pixels, mm, sides)
                if error > 0.35 or abs(fitted[3]) > 0.6:
                    continue
                widen = fitted[3]
                points = []
                for (a, b), (lo, hi) in zip(gaps, run):
                    if reverse:
                        points += [(edge.point(hi - widen), a), (edge.point(lo + widen), b)]
                    else:
                        points += [(edge.point(lo + widen), a), (edge.point(hi - widen), b)]
                found.append((error, edge, points))
    return found


@dataclasses.dataclass
class Scan:
    mat: Mat
    image: object                 # PIL image of the photo area, true size
    pixels_per_mm: float
    residual_mm: float            # RMS of the fit, on the mat
    worst_mm: float
    points: int
    lines: tuple[str, ...]        # Studio's names for the bars that were read

    @property
    def size_mm(self) -> tuple[float, float]:
        return self.mat.photo_size

    def report(self) -> str:
        return (f"{self.mat.describe()}: {self.points} gap edges on {', '.join(self.lines)}, "
                f"fit within {self.residual_mm:.2f} mm (worst {self.worst_mm:.2f} mm).")

    def sticker_shape(self, name: str) -> model.Shape:
        """The straightened photo as a Design image, at true size, not cut."""
        data = io.BytesIO()
        self.image.convert("RGBA").save(data, "PNG")
        sticker = sticker_assets.Sticker(base64.b64encode(data.getvalue()).decode("ascii"), name)
        sticker.validate()
        width, height = self.size_mm
        return model.Shape("Sticker", 0, 0, width, height, sticker=sticker, action="None")


def check_fit(residual_mm: float, worst_mm: float, lines: int) -> str | None:
    """Why a fitted scan cannot be used, or None. A wrong fit cuts at the wrong size."""
    if lines < MIN_LINES:
        return "Fewer than two marker edges were recognised. Photograph the whole mat, square on, in even light."
    if residual_mm > MAX_RESIDUAL_MM or worst_mm > MAX_POINT_ERROR_MM:
        return (f"The markers fit only to within {residual_mm:.2f} mm (worst {worst_mm:.2f} mm). "
                "Retake the photo flatter and closer to straight above the mat.")
    return None


def _fit(detected, mat):
    """The best consistent set of identified lines for one mat."""
    candidates = []
    for gaps in detected:
        options = _candidates(gaps, mat)
        if options:
            candidates.append(sorted(options, key=lambda item: item[0]))
    best = None
    # Seed with each pair of non-parallel candidates, then add every other
    # detected line whose best option agrees with the seeded perspective.
    flat = [(i, option) for i, options in enumerate(candidates) for option in options[:4]]
    for (i, first), (j, second) in ((a, b) for a in flat for b in flat if a[0] < b[0]):
        if _parallel(first[1], second[1]):
            continue
        points = first[2] + second[2]
        try:
            h = homography([p for p, _ in points], [q for _, q in points])
        except PixScanError:
            continue
        used = {_key(first[1]): first, _key(second[1]): second}
        for k, options in enumerate(candidates):
            if k in (i, j):
                continue
            for option in options:
                if _key(option[1]) in used:
                    continue
                if max(math.dist(apply(h, p), q) for p, q in option[2]) <= _pixel_tolerance(h):
                    used[_key(option[1])] = option
                    break
        chosen = [p for option in used.values() for p in option[2]]
        try:
            h = homography([p for p, _ in chosen], [q for _, q in chosen])
            inverse = _inverse(h)
        except PixScanError:
            continue
        errors = [math.dist(apply(inverse, q), p) for p, q in chosen]
        rms = math.sqrt(sum(e * e for e in errors) / len(errors))
        lines = tuple(name for option in used.values() for name in option[1].names)
        score = (-len(used), rms)
        if best is None or score < best[0]:
            best = (score, h, rms, max(errors), len(chosen), lines)
    return best


def _parallel(a, b):
    return a.axis == b.axis


def _key(edge):
    return edge.axis, edge.constant


def _pixel_tolerance(h):
    # One millimetre on the mat, in detection pixels, near the frame origin.
    a, b = apply(h, (0, 0)), apply(h, (1, 0))
    return max(2.5, 1.0 * math.dist(a, b))


def scan(image, *, pixels_per_mm: float = 8.0) -> Scan:
    """Find the mat in `image` (a PIL image) and return its photo area, straightened."""
    from PIL import Image, ImageOps

    photo = ImageOps.exif_transpose(image).convert("RGB")
    factor = min(1.0, DETECT_EDGE_PX / max(photo.size))
    small = photo.resize((max(1, round(photo.width * factor)), max(1, round(photo.height * factor))),
                         Image.Resampling.LANCZOS) if factor < 1 else photo
    detected = [_gap_edges(members) for members in _group(_segments(_dark_mask(small.convert("L"))))]
    detected = [gaps for gaps in detected if len(gaps) >= 3]
    results = []
    for mat in MATS.values():
        fitted = _fit(detected, mat)
        if fitted:
            results.append((fitted[0], mat, fitted))
    if not results:
        raise PixScanError("No PixScan markers were found. Photograph the whole mat, square on, in even light.")
    _, mat, (score, h, rms, worst, points, lines) = min(results, key=lambda item: item[0])
    edges = -score[0]
    problem = check_fit(rms, worst, edges)
    if problem:
        raise PixScanError(problem)
    width, height = mat.photo_size
    size = (max(1, round(width * pixels_per_mm)), max(1, round(height * pixels_per_mm)))
    # Output pixel → mat mm → detection pixel → photo pixel.
    to_photo = _mul(((1 / factor, 0, 0), (0, 1 / factor, 0), (0, 0, 1)),
                    _mul(h, ((1 / pixels_per_mm, 0, 0), (0, 1 / pixels_per_mm, 0), (0, 0, 1))))
    coefficients = [value / to_photo[2][2] for row in to_photo for value in row][:8]
    straight = photo.transform(size, Image.Transform.PERSPECTIVE, coefficients,
                               Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
    return Scan(mat, straight, pixels_per_mm, rms, worst, points, lines)


# ---- synthetic mats, for tests ---------------------------------------------

def render(mat: Mat, pixels_per_mm: float = 4.0, margin_mm: float = 12.0):
    """The mat's markers drawn flat, in mat coordinates, as this module reads them.

    Used to test the detector on known geometry. It is a drawing of the
    recovered numbers, not a picture of a real mat."""
    from PIL import Image, ImageDraw

    xs = [c for line in mat.lines for c in (line.start[0], line.end[0])]
    ys = [c for line in mat.lines for c in (line.start[1], line.end[1])]
    left, top = min(xs) - margin_mm, min(ys) - margin_mm
    size = (round((max(xs) + margin_mm - left) * pixels_per_mm), round((max(ys) + margin_mm - top) * pixels_per_mm))
    image = Image.new("RGB", size, (236, 236, 232))
    draw = ImageDraw.Draw(image)
    half = MARKER_THICKNESS_MM / 2
    for line in mat.lines:
        for a, b in line.bars():
            (x1, y1), (x2, y2) = line.at(a), line.at(b)
            box = (min(x1, x2) - (half if x1 == x2 else 0), min(y1, y2) - (half if y1 == y2 else 0),
                   max(x1, x2) + (half if x1 == x2 else 0), max(y1, y2) + (half if y1 == y2 else 0))
            draw.rectangle([((box[0] - left) * pixels_per_mm, (box[1] - top) * pixels_per_mm),
                            ((box[2] - left) * pixels_per_mm - 1, (box[3] - top) * pixels_per_mm - 1)],
                           fill=(20, 20, 20))
    return image, (left, top)
