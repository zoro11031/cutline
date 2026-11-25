"""Point editing, as Silhouette Studio's Point Editing mode does it.

A shape is edited as nodes on the page: points, each with an optional pair
of Bezier handles and a smooth flag. The operations are those of Studio's
Point Editing toolbar (user manual chapter 04): move and add points, Delete
Point, Break Path, rejoin two ends, Corner, Smooth, Make Flat, Make Curve.
Results go back into the document as a Path, whose `paths()` flattens the
curves for everything downstream.
"""

from __future__ import annotations

import dataclasses
import math
from dataclasses import replace

from . import editor_model as model

KAPPA = 0.5522847498  # handle length for a quarter circle, as a fraction of the radius
EDITABLE_KINDS = ("Path", "Line", "Rectangle", "Rounded rectangle", "Ellipse")


@dataclasses.dataclass(frozen=True)
class Nodes:
    points: tuple
    handles: tuple      # per point: () or (in handle, out handle, smooth), page coordinates
    closed: bool

    def handle(self, i):
        """(in, out, smooth) for point i, a plain corner giving its own position twice."""
        entry = self.handles[i] if self.handles else ()
        return entry if entry else (self.points[i], self.points[i], False)

    def segments(self):
        count = len(self.points)
        return range(count if self.closed else count - 1)

    def curve(self, i):
        """The cubic from point i to the next: (a, c1, c2, b)."""
        j = (i + 1) % len(self.points)
        return self.points[i], self.handle(i)[1], self.handle(j)[0], self.points[j]


def _with(nodes, points=None, handles=None, closed=None):
    points = nodes.points if points is None else tuple(points)
    handles = nodes.handles if handles is None else tuple(handles)
    if handles and all(not h or (h[0] == p and h[1] == p and not h[2]) for h, p in zip(handles, points)):
        handles = ()
    return Nodes(points, handles, nodes.closed if closed is None else closed)


def _full(nodes):
    return [nodes.handle(i) for i in range(len(nodes.points))]


# ---- shapes to nodes and back ---------------------------------------------

def editable(shape):
    return shape.kind in EDITABLE_KINDS and shape.visible


def nodes(shape) -> Nodes:
    """The shape's points and handles on the page, its rotation and flips applied."""
    if not editable(shape):
        raise ValueError("Release or convert this object to a path before editing its points.")
    x, y, w, h = shape.x, shape.y, shape.width, shape.height
    if shape.kind == "Path":
        points = [(x + px * w, y + py * h) for px, py in shape.points]
        handles = [((x + e[0][0] * w, y + e[0][1] * h), (x + e[1][0] * w, y + e[1][1] * h), e[2]) if e else ()
                   for e in shape.handles] if shape.handles else []
        closed = shape.closed
    elif shape.kind == "Line":
        points, handles, closed = [(x, y), (x + w, y + h)], [], False
    elif shape.kind == "Rectangle":
        points, handles, closed = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)], [], True
    elif shape.kind == "Ellipse":
        rx, ry, cx, cy = w / 2, h / 2, x + w / 2, y + h / 2
        points = [(cx + rx, cy), (cx, cy + ry), (cx - rx, cy), (cx, cy - ry)]
        kx, ky = rx * KAPPA, ry * KAPPA
        handles = [((cx + rx, cy - ky), (cx + rx, cy + ky), True), ((cx + kx, cy + ry), (cx - kx, cy + ry), True),
                   ((cx - rx, cy + ky), (cx - rx, cy - ky), True), ((cx - kx, cy - ry), (cx + kx, cy - ry), True)]
        closed = True
    else:  # rounded rectangle: eight points, the corners curved
        r = max(0.0, min(shape.radius, w / 2, h / 2))
        k = r * KAPPA
        points = [(x + r, y), (x + w - r, y), (x + w, y + r), (x + w, y + h - r),
                  (x + w - r, y + h), (x + r, y + h), (x, y + h - r), (x, y + r)]
        handles = [((x + r - k, y), (x + r, y), False), ((x + w - r, y), (x + w - r + k, y), False),
                   ((x + w, y + r - k), (x + w, y + r), False), ((x + w, y + h - r), (x + w, y + h - r + k), False),
                   ((x + w - r + k, y + h), (x + w - r, y + h), False), ((x + r, y + h), (x + r - k, y + h), False),
                   ((x, y + h - r + k), (x, y + h - r), False), ((x, y + r), (x, y + r - k), False)]
        closed = True
    flat = list(points) + [p for entry in handles if entry for p in entry[:2]]
    moved = list(shape._cut_path(flat, False).points)
    placed, rest = moved[:len(points)], moved[len(points):]
    out = []
    for entry in handles:
        if entry:
            out.append((rest.pop(0), rest.pop(0), entry[2]))
        else:
            out.append(())
    return _with(Nodes(tuple(placed), tuple(out), closed))


def to_shape(nodes, original) -> model.Shape:
    """Nodes as a Path, keeping the original's style, action, group and holder."""
    if len(set(nodes.points)) < (3 if nodes.closed else 2):
        raise ValueError("A path needs at least two points, or three when closed.")
    every = list(nodes.points) + [p for entry in nodes.handles if entry for p in entry[:2]]
    xs, ys = [p[0] for p in every], [p[1] for p in every]
    x, y = min(xs), min(ys)
    w, h = max(xs) - x, max(ys) - y
    if not all(model._number(v) for v in (x, y, w, h)):
        raise ValueError("The points are outside the design workspace's editable range.")
    norm = lambda p: ((p[0] - x) / (w or 1), (p[1] - y) / (h or 1))  # noqa: E731
    handles = tuple((norm(e[0]), norm(e[1]), e[2]) if e else () for e in nodes.handles)
    return replace(original, kind="Path", x=x, y=y, width=w, height=h, rotation=0, flip_x=False, flip_y=False,
                   points=tuple(norm(p) for p in nodes.points), handles=handles, closed=nodes.closed,
                   radius=model.Shape.radius, contours=(), text=None, sticker=None)


def from_clicks(points, *, closed, smooth=True) -> Nodes:
    """Points placed by the Curve tool, joined by smooth curves through each."""
    points = tuple(points)
    count = len(points)
    if not smooth or count < 3:
        return Nodes(points, (), closed)
    handles = []
    for i, p in enumerate(points):
        if not closed and i in (0, count - 1):
            neighbour = points[1] if i == 0 else points[-2]
            tx, ty = (neighbour[0] - p[0], neighbour[1] - p[1]) if i == 0 else (p[0] - neighbour[0], p[1] - neighbour[1])
            tx, ty = tx / 3, ty / 3
        else:
            before, after = points[(i - 1) % count], points[(i + 1) % count]
            tx, ty = (after[0] - before[0]) / 6, (after[1] - before[1]) / 6
        handles.append(((p[0] - tx, p[1] - ty), (p[0] + tx, p[1] + ty), True))
    return Nodes(points, tuple(handles), closed)


# ---- the Point Editing toolbar ---------------------------------------------

def move_point(nodes, i, position):
    dx, dy = position[0] - nodes.points[i][0], position[1] - nodes.points[i][1]
    points = list(nodes.points)
    points[i] = position
    handles = _full(nodes)
    a, b, smooth = handles[i]
    handles[i] = ((a[0] + dx, a[1] + dy), (b[0] + dx, b[1] + dy), smooth)
    return _with(nodes, points, handles)


def step_point(current, count, direction):
    """Cycle in outline order, starting at the corresponding end."""
    if current is None or not 0 <= current < count:
        return 0 if direction > 0 else count - 1
    return (current + direction) % count


def nudge_point(shape, outline, index, dx, dy):
    x, y = outline.points[index]
    return to_shape(move_point(outline, index, (x + dx, y + dy)), shape)


def move_handle(nodes, i, side, position):
    """Drag the in (0) or out (1) handle of point i; a smooth point keeps the
    other handle opposite, at its own length."""
    handles = _full(nodes)
    entry = list(handles[i])
    entry[side] = position
    if entry[2]:
        p = nodes.points[i]
        other = entry[1 - side]
        length = math.dist(p, other)
        dx, dy = position[0] - p[0], position[1] - p[1]
        norm = math.hypot(dx, dy)
        if norm and length:
            entry[1 - side] = (p[0] - dx / norm * length, p[1] - dy / norm * length)
    handles[i] = tuple(entry)
    return _with(nodes, handles=handles)


def _point_on(curve, t):
    a, c1, c2, b = curve
    u = 1 - t
    return tuple(u ** 3 * a[k] + 3 * u * u * t * c1[k] + 3 * u * t * t * c2[k] + t ** 3 * b[k] for k in (0, 1))


def nearest_segment(nodes, position):
    """(distance, segment, t) of the outline point closest to `position`."""
    best = None
    for i in nodes.segments():
        curve = nodes.curve(i)
        steps = 64
        for step in range(steps + 1):
            t = step / steps
            d = math.dist(_point_on(curve, t), position)
            if best is None or d < best[0]:
                best = (d, i, t)
    d, i, t = best
    # Refine t by halving the bracket around the coarse best.
    lo, hi = max(0.0, t - 1 / 64), min(1.0, t + 1 / 64)
    curve = nodes.curve(i)
    for _ in range(24):
        m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        if math.dist(_point_on(curve, m1), position) < math.dist(_point_on(curve, m2), position):
            hi = m2
        else:
            lo = m1
    t = (lo + hi) / 2
    return math.dist(_point_on(curve, t), position), i, t


def insert_point(nodes, position, tolerance):
    """Add a point on the outline where `position` is closest; returns (nodes, index)."""
    distance, i, t = nearest_segment(nodes, position)
    if distance > tolerance or t <= 1e-6 or t >= 1 - 1e-6:
        raise ValueError("Click on the outline, between two points, to add a point.")
    a, c1, c2, b = nodes.curve(i)
    j = (i + 1) % len(nodes.points)
    lerp = lambda p, q: (p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t)  # noqa: E731
    ab, bc, cd = lerp(a, c1), lerp(c1, c2), lerp(c2, b)
    abc, bcd = lerp(ab, bc), lerp(bc, cd)
    new = lerp(abc, bcd)
    handles = _full(nodes)
    straight = c1 == a and c2 == b
    if straight:
        entry = (new, new, False)
    else:
        handles[i] = (handles[i][0], ab, handles[i][2])
        handles[j] = (cd, handles[j][1], handles[j][2])
        entry = (abc, bcd, True)
    points = list(nodes.points)
    points.insert(i + 1, new)
    handles.insert(i + 1, entry)
    return _with(nodes, points, handles), i + 1


def delete_point(nodes, i):
    minimum = 3 if nodes.closed else 2
    if len(nodes.points) <= minimum:
        raise ValueError("A path needs at least two points, or three when closed. Delete the object instead.")
    points, handles = list(nodes.points), _full(nodes)
    del points[i], handles[i]
    return _with(nodes, points, handles)


def break_path(nodes, i):
    """Studio's Break Path: a closed path opens at point i; an open path splits
    in two there. Returns a tuple of node sets."""
    points, handles = list(nodes.points), _full(nodes)
    p, (a, b, _smooth) = points[i], handles[i]
    if nodes.closed:
        order = list(range(i, len(points))) + list(range(0, i))
        new_points = [points[k] for k in order] + [p]
        new_handles = [handles[k] for k in order] + [(a, p, False)]
        new_handles[0] = (p, b, False)
        return (_with(nodes, new_points, new_handles, closed=False),)
    if i in (0, len(points) - 1):
        raise ValueError("This is already an end of the path. Choose a point between the ends.")
    first = _with(nodes, points[:i + 1], handles[:i] + [(a, p, False)], closed=False)
    second = _with(nodes, points[i:], [(p, b, False)] + handles[i + 1:], closed=False)
    return first, second


def join_ends(nodes, tolerance):
    """An open path whose two ends meet closes, as dragging one end onto the other does in Studio."""
    if nodes.closed or len(nodes.points) < 3 or math.dist(nodes.points[0], nodes.points[-1]) > tolerance:
        return None
    handles = _full(nodes)
    first, last = handles[0], handles[-1]
    handles[0] = (last[0], first[1], False)
    return _with(nodes, nodes.points[:-1], handles[:-1], closed=True)


def make_curve(nodes, i):
    """Studio's Make Curve: the segment after point i gets handles a third of the way along."""
    if i not in nodes.segments():
        raise ValueError("Choose a point that starts a segment.")
    a, c1, c2, b = nodes.curve(i)
    if c1 != a or c2 != b:
        return nodes  # already a curve: Make Curve leaves its shape alone
    j = (i + 1) % len(nodes.points)
    handles = _full(nodes)
    third = ((b[0] - a[0]) / 3, (b[1] - a[1]) / 3)
    handles[i] = (handles[i][0], (a[0] + third[0], a[1] + third[1]), handles[i][2])
    handles[j] = ((b[0] - third[0], b[1] - third[1]), handles[j][1], handles[j][2])
    return _with(nodes, handles=handles)


def make_flat(nodes, i):
    """Studio's Make Flat: the segment after point i becomes a straight line."""
    if i not in nodes.segments():
        raise ValueError("Choose a point that starts a segment.")
    j = (i + 1) % len(nodes.points)
    handles = _full(nodes)
    handles[i] = (handles[i][0], nodes.points[i], False)
    handles[j] = (nodes.points[j], handles[j][1], False)
    return _with(nodes, handles=handles)


def smooth(nodes, i):
    """Studio's Smooth: the handles line up through the point."""
    p = nodes.points[i]
    a, b, _ = nodes.handle(i)
    count = len(nodes.points)
    if a == p and b == p:
        before = nodes.points[(i - 1) % count] if nodes.closed or i > 0 else p
        after = nodes.points[(i + 1) % count] if nodes.closed or i < count - 1 else p
        dx, dy = (after[0] - before[0]) / 6, (after[1] - before[1]) / 6
        if not dx and not dy:
            raise ValueError("This point has no neighbours to smooth between.")
        a, b = (p[0] - dx, p[1] - dy), (p[0] + dx, p[1] + dy)
    else:
        dx, dy = b[0] - a[0], b[1] - a[1]
        norm = math.hypot(dx, dy) or 1
        la, lb = math.dist(p, a), math.dist(p, b)
        a = (p[0] - dx / norm * la, p[1] - dy / norm * la)
        b = (p[0] + dx / norm * lb, p[1] + dy / norm * lb)
    handles = _full(nodes)
    handles[i] = (a, b, True)
    return _with(nodes, handles=handles)


def corner(nodes, i):
    """Studio's Corner: the handles move independently."""
    handles = _full(nodes)
    handles[i] = (handles[i][0], handles[i][1], False)
    return _with(nodes, handles=handles)


def nearest_node(nodes, position):
    """(distance, kind, index, side): kind is "point" or "handle"."""
    best = (math.inf, None, None, None)
    for i, p in enumerate(nodes.points):
        d = math.dist(p, position)
        if d < best[0]:
            best = (d, "point", i, None)
        if nodes.handles and nodes.handles[i]:
            for side in (0, 1):
                q = nodes.handles[i][side]
                if q != p:
                    d = math.dist(q, position)
                    if d < best[0]:
                        best = (d, "handle", i, side)
    return best


# ---- shapes the drawing tools make ----------------------------------------------

def arc(start, end) -> Nodes:
    """Studio's Arc tool: a quarter ellipse from `start` to `end`, leaving
    `start` horizontally and arriving at `end` vertically."""
    (sx, sy), (ex, ey) = start, end
    out = (sx + KAPPA * (ex - sx), sy)
    into = (ex, ey - KAPPA * (ey - sy))
    return Nodes((start, end), ((start, out, False), (into, end, False)), False)


def regular_polygon(left, top, width, height, sides) -> Nodes:
    """Studio's Regular Polygon: `sides` corners on the ellipse in the box, the first at the top."""
    sides = max(3, min(int(sides), 64))
    cx, cy, rx, ry = left + width / 2, top + height / 2, width / 2, height / 2
    points = tuple((cx + rx * math.cos(-math.pi / 2 + 2 * math.pi * k / sides),
                    cy + ry * math.sin(-math.pi / 2 + 2 * math.pi * k / sides)) for k in range(sides))
    return Nodes(points, (), True)


def stretch_selection(shapes, indices, anchor, axis, factor):
    """Studio's side handles: stretch along one axis (0 = x, 1 = y) from `anchor`.

    A shape square to the page keeps its kind; one at another angle becomes a
    path, since a stretched rotated rectangle is no longer a rectangle."""
    if not indices or factor == 1:
        return shapes
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("Drag away from the opposite side to stretch.")
    result = list(shapes)
    for index in indices:
        shape = shapes[index]
        quarter = round(shape.rotation % 180, 6)
        if quarter in (0, 90):
            frame_axis = axis if quarter == 0 else 1 - axis
            size = "width" if frame_axis == 0 else "height"
            centre = (shape.x + shape.width / 2, shape.y + shape.height / 2)
            moved = anchor[axis] + (centre[axis] - anchor[axis]) * factor
            new_size = getattr(shape, size) * factor
            changes = {size: new_size}
            other_centre = list(centre)
            other_centre[axis] = moved
            changes["x"] = other_centre[0] - (new_size if size == "width" else shape.width) / 2
            changes["y"] = other_centre[1] - (new_size if size == "height" else shape.height) / 2
            stretched = replace(shape, **changes)
        elif editable(shape):
            n = nodes(shape)
            scale = lambda p: tuple(anchor[k] + (p[k] - anchor[k]) * factor if k == axis else p[k]  # noqa: E731
                                    for k in (0, 1))
            n = Nodes(tuple(scale(p) for p in n.points),
                      tuple((scale(e[0]), scale(e[1]), e[2]) if e else () for e in n.handles), n.closed)
            stretched = to_shape(n, shape)
        else:
            raise ValueError("Rotate this object square to the page before stretching it, or scale it from a corner.")
        if not all(model._number(v) for v in (stretched.x, stretched.y, stretched.width, stretched.height)):
            raise ValueError("This stretch would place a shape outside the editable range.")
        if stretched.kind != "Line" and min(abs(stretched.width), abs(stretched.height)) < 0.1 and stretched.kind != "Path":
            raise ValueError("The shape is too small to stretch further.")
        result[index] = stretched
    return tuple(result)


def draw_regular_polygon(start, end, sides, *, shift=False, alt=False):
    dx, dy = end[0] - start[0], end[1] - start[1]
    if shift:
        side = max(abs(dx), abs(dy))
        dx, dy = math.copysign(side, dx or 1), math.copysign(side, dy or 1)
    width, height = abs(dx), abs(dy)
    if min(width, height) < 0.1:
        return None
    if alt:
        left, top = start[0] - width, start[1] - height
        width, height = width * 2, height * 2
    else:
        left, top = min(start[0], start[0] + dx), min(start[1], start[1] + dy)
    return to_shape(regular_polygon(left, top, width, height, sides), model.Shape("Path"))


def smooth_freehand(points, scale, *, final=False):
    closed = final and len(points) > 3 and math.dist(points[0], points[-1]) <= 8 / scale
    if closed:
        points = points[:-1]
    simple = model.simplified_points(tuple(points), max(0.3, 2 / scale), closed=closed)
    return to_shape(from_clicks(simple, closed=closed and len(simple) >= 3), model.Shape("Path"))
