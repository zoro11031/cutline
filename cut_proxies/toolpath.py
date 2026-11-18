"""Cut order, pass direction and seam overlap in sheet millimetres."""

from __future__ import annotations

import math

from .template import CutPath

PATH_ORDERS = {
    "design": "No Sort",
    "nearest": "Maximize Speed",
    "rollers": "Minimize Roller Movement",
}
PASS_ORDERS = {"sheet": "Repeat the sheet", "path": "Finish each path"}

WEED_LINES = {"none": "None", "horizontal": "Horizontal",
              "vertical": "Vertical", "grid": "Horizontal and vertical"}
_WEED_LABELS = frozenset(("Weed border", "Weed line"))


def is_weed_path(path):
    return path.label in _WEED_LABELS


def _gaps(intervals):
    """Return midpoints of gaps between disjoint projected contour bounds."""
    intervals = sorted(intervals)
    end = intervals[0][1]
    for lower, upper in intervals[1:]:
        if lower > end:
            yield (lower + end) / 2
        end = max(end, upper)


def weed_paths(paths, padding, lines):
    """Put a padded border and divider lines around the supplied contours."""
    if not math.isfinite(padding) or padding < 0 or lines not in WEED_LINES:
        raise ValueError("Choose a valid Weed Lines mode and a finite, nonnegative padding.")
    if not padding:
        if lines != "none":
            raise ValueError("Set positive Weed Border padding to add Weed Lines.")
        return ()
    bounds = []
    for path in paths:
        if path.points:
            x, y = zip(*path.points)
            bounds.append((min(x), min(y), max(x), max(y)))
    if not bounds:
        raise ValueError("Weed Border requires cut geometry.")
    left = min(b[0] for b in bounds) - padding
    top = min(b[1] for b in bounds) - padding
    right = max(b[2] for b in bounds) + padding
    bottom = max(b[3] for b in bounds) + padding
    extras = []
    if lines in ("horizontal", "grid"):
        extras.extend(CutPath(((left, y), (right, y)), False, "Weed line")
                      for y in _gaps((b[1], b[3]) for b in bounds))
    if lines in ("vertical", "grid"):
        extras.extend(CutPath(((x, top), (x, bottom)), False, "Weed line")
                      for x in _gaps((b[0], b[2]) for b in bounds))
    extras.append(CutPath(((left, top), (right, top), (right, bottom), (left, bottom)), True,
                          "Weed border"))
    return tuple(extras)



def _lead(points, distance):
    """Return points along a contour up to distance, limited to one lap."""
    lead = [points[0]]
    for a, b in zip(points, points[1:]):
        length = math.dist(a, b)
        if length == 0:
            continue
        if distance < length:
            fraction = distance / length
            lead.append(tuple(x + (y - x) * fraction for x, y in zip(a, b)))
            break
        lead.append(b)
        distance -= length
        if distance == 0:
            break
    return tuple(lead)


def _edges(points, closed):
    next_vertices = points[1:] + (points[:1] if closed else ())
    return tuple(zip(points, next_vertices))


def _orient(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _touches(a, b, p):
    return (abs(_orient(a, b, p)) <= 1e-9
            and all(min(x, y) - 1e-9 <= z <= max(x, y) + 1e-9
                    for x, y, z in zip(a, b, p)))


def _intersects(a, b, c, d):
    return ((_orient(a, b, c) * _orient(a, b, d) < 0
             and _orient(c, d, a) * _orient(c, d, b) < 0)
            or _touches(a, b, c) or _touches(a, b, d)
            or _touches(c, d, a) or _touches(c, d, b))


def _inside(point, edges):
    x, y = point
    return sum((a[1] > y) != (b[1] > y)
               and x < a[0] + (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1])
               for a, b in edges) % 2 == 1


def _interior_depths(contours):
    """Count strict parents. A path touching or crossing a border is separate."""
    prepared = []
    for i, points, closed in contours:
        xs, ys = zip(*points)
        prepared.append((i, points, closed, (min(xs), min(ys), max(xs), max(ys)),
                         _edges(points, closed)))
    depths = {}
    for i, points, _closed, (x, y, x2, y2), edges in prepared:
        depths[i] = sum(
            closed and j != i and a < x <= x2 < c and b < y <= y2 < d
            and _inside(points[0], border)
            and not any(_intersects(*left, *right) for left in edges for right in border)
            for j, _, closed, (a, b, c, d), border in prepared)
    return depths


def _ordered(contours, settings, start):
    depths = (_interior_depths(contours)
              if settings.interior_first or settings.delicate_media else {})
    if settings.path_order == "rollers":
        return sorted(contours, key=lambda c: (-depths.get(c[0], 0), c[1][0][1], c[1][0][0]))
    if settings.path_order == "design":
        return sorted(contours, key=lambda c: -depths.get(c[0], 0)) if depths else contours

    ordered = []
    while contours:
        index = min(range(len(contours)),
                    key=lambda i: (-depths.get(contours[i][0], 0),
                                   math.dist(start, contours[i][1][0])))
        contour = contours.pop(index)
        ordered.append(contour)
        start = contour[1][0] if contour[2] else contour[1][-1]
    return ordered


def _alternate_winding(contours):
    """Keep authored starts and alternate drag direction on closed contours."""
    clockwise = None
    for i, points, closed in contours:
        area = (sum(a[0] * b[1] - b[0] * a[1] for a, b in _edges(points, True))
                if closed else 0)
        if area:
            if clockwise is not None and (area > 0) != clockwise:
                points = points[:1] + points[1:][::-1]
            clockwise = not (area > 0) if clockwise is None else not clockwise
        yield i, points, closed


def schedule(paths, settings, start=(0.0, 0.0)):
    """Return (source index, pen-down vertices) in the order the cutter uses.

    The nearest-start strategy is based on inkscape-silhouette's
    StrategyMinTraveling.py. It keeps each contour's authored entry and winding.
    Pass grouping and seam overlap follow sendto_silhouette.py's
    multipassOvercut. Each overlap follows the source contour.
    """
    artwork = []
    weeding = []
    for i, path in enumerate(paths):
        if path.points:
            contour = (i, tuple(path.points), path.closed)
            (weeding if is_weed_path(path) else artwork).append(contour)
    contours = _ordered(artwork, settings, start)
    if settings.delicate_media:
        contours = list(_alternate_winding(contours))
    contours.extend(weeding)

    def motion(points, closed, first_pass, pass_count):
        path = points + (points[0],) if closed else points
        runs = tuple(tuple(reversed(path)) if settings.alternate_direction and n % 2 else path
                     for n in range(first_pass, first_pass + pass_count))
        vertices = runs[0]
        for run in runs[1:]:
            vertices += run[1:]
        if closed and settings.overcut_mm:
            prefix = _lead(tuple(reversed(runs[0])), settings.overcut_mm)
            suffix = _lead(runs[-1], settings.overcut_mm)
            vertices = tuple(reversed(prefix[1:])) + vertices + suffix[1:]
        return vertices

    if settings.pass_order == "path":
        traversals = []
        for i, points, closed in contours:
            repeats = settings.passes if closed or settings.alternate_direction else 1
            for start_pass in range(0, settings.passes, repeats):
                traversals.append((i, motion(points, closed, start_pass, repeats)))
        return tuple(traversals)
    return tuple((i, motion(points, closed, pass_index, 1))
                 for pass_index in range(settings.passes) for i, points, closed in contours)
