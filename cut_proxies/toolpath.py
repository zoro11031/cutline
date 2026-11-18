"""Cut order, pass direction and seam overlap in sheet millimetres."""

from __future__ import annotations

import math

PATH_ORDERS = {"design": "Design order", "nearest": "Nearest path"}
PASS_ORDERS = {"sheet": "Repeat the sheet", "path": "Finish each path"}


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


def schedule(paths, settings, start=(0.0, 0.0)):
    """Return (source index, pen-down vertices) in the order the cutter uses.

    The nearest-start strategy is based on inkscape-silhouette's
    StrategyMinTraveling.py. It keeps each contour's authored entry and winding.
    Pass grouping and seam overlap follow sendto_silhouette.py's
    multipassOvercut. Each overlap follows the source contour.
    """
    contours = [(i, tuple(path.points), path.closed)
                for i, path in enumerate(paths) if path.points]

    if settings.path_order == "nearest":
        ordered = []
        while contours:
            index = min(range(len(contours)), key=lambda i: math.dist(start, contours[i][1][0]))
            contour = contours.pop(index)
            ordered.append(contour)
            start = contour[1][0] if contour[2] else contour[1][-1]
        contours = ordered

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
