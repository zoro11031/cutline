"""Silhouette Studio's Transform and Replicate panels (user manual chapter 06).

Every function takes the document's shapes and the selected indices and
returns new shapes (and, where copies are made, which indices are new), so
Design can commit the result as one undoable step.
"""

from __future__ import annotations

import math
from dataclasses import replace

from . import editor_model as model, path_edit, template

SIDES = ("left", "right", "above", "below")


def _box(shapes, indices):
    if not indices:
        raise ValueError("Select an object first.")
    return model.bounds(shapes[i] for i in indices)


def _centre(shapes, indices):
    box = _box(shapes, indices)
    return box.x + box.w / 2, box.y + box.h / 2


def _append(shapes, copies):
    """Copies added after the originals, their groups renumbered."""
    out = model.paste(shapes, copies)
    return out, tuple(range(len(shapes), len(out)))


# ---- Transform: move, rotate, scale, shear ------------------------------------

def move_by(shapes, indices, dx, dy):
    _box(shapes, indices)
    return model.move(shapes, indices, dx, dy)


def move_to(shapes, indices, x, y):
    box = _box(shapes, indices)
    return model.move(shapes, indices, x - box.x, y - box.y)


def rotate_by(shapes, indices, angle, centre=None):
    """Turn the selection as one, about its centre or a chosen centre of rotation."""
    _box(shapes, indices)
    return model.rotate_selection(shapes, indices, angle, center=centre or _centre(shapes, indices))


def rotate_to(shapes, indices, angle):
    """Studio's Rotate To: each object to an absolute angle about its own centre."""
    _box(shapes, indices)
    out = shapes
    for i in indices:
        out = model.rotate_selection(out, (i,), (angle - out[i].rotation) % 360)
    return out


def scale_by(shapes, indices, percent):
    if not math.isfinite(percent) or percent <= 0:
        raise ValueError("Enter a scale above 0%.")
    return model.scale_selection(shapes, indices, _centre(shapes, indices), percent / 100)


def _map_shape(shape, mapping):
    """Apply a point mapping to a shape, returning a path (or a compound for text and compounds)."""
    if path_edit.editable(shape):
        nodes = path_edit.nodes(shape)
        moved = path_edit.Nodes(tuple(mapping(p) for p in nodes.points),
                                tuple((mapping(e[0]), mapping(e[1]), e[2]) if e else () for e in nodes.handles),
                                nodes.closed)
        return path_edit.to_shape(moved, shape)
    if shape.kind in ("Compound", "Text"):
        paths = tuple(template.CutPath(tuple(mapping(p) for p in path.points), True) for path in shape.paths())
        return replace(model.styled_contours(paths, shape, compound=True), group=shape.group)
    raise ValueError("Images can be moved, scaled and rotated, but not sheared or distorted.")


def shear(shapes, indices, horizontal, vertical):
    """Studio's Shear: slant by angles in degrees about the selection's centre."""
    if not all(math.isfinite(v) and -89 < v < 89 for v in (horizontal, vertical)):
        raise ValueError("Enter shear angles between -89° and 89°.")
    if any(shapes[i].kind == "Sticker" for i in indices):
        raise ValueError("Images can be moved, scaled and rotated, but not sheared or distorted.")
    if not horizontal and not vertical:
        return shapes
    cx, cy = _centre(shapes, indices)
    th, tv = math.tan(math.radians(horizontal)), math.tan(math.radians(vertical))

    def slant(point):
        x, y = point[0] - cx, point[1] - cy
        return cx + x + th * y, cy + tv * x + y

    out = list(shapes)
    for i in indices:
        out[i] = _map_shape(shapes[i], slant)
    return tuple(out)


# ---- Mirror and Replicate ------------------------------------------------------

def _offset(box, side, gap):
    return {"left": (-(box.w + gap), 0), "right": (box.w + gap, 0),
            "above": (0, -(box.h + gap)), "below": (0, box.h + gap)}[side]


def mirror_copy(shapes, indices, side, gap=0.0):
    """Studio's Mirror menu: a flipped copy placed to one side."""
    box = _box(shapes, indices)
    copies = tuple(shapes[i] for i in sorted(indices))
    flipped = model.transform_selection(copies, range(len(copies)), "horizontal" if side in ("left", "right")
                                        else "vertical")
    return _append(shapes, model.move(flipped, range(len(flipped)), *_offset(box, side, gap)))


def duplicate_side(shapes, indices, side, gap=0.0):
    """Studio's Duplicate Left / Right / Above / Below."""
    box = _box(shapes, indices)
    copies = tuple(shapes[i] for i in sorted(indices))
    return _append(shapes, model.move(copies, range(len(copies)), *_offset(box, side, gap)))


def replicate_line(shapes, indices, count, axis, gap=0.0):
    """Studio's Row of Three/Four (axis 0) and Column of Three/Four (axis 1): `count` in all."""
    box = _box(shapes, indices)
    step = (box.w + gap, 0) if axis == 0 else (0, box.h + gap)
    original = tuple(shapes[i] for i in sorted(indices))
    copies = ()
    for k in range(1, count):
        copies += model.move(original, range(len(original)), step[0] * k, step[1] * k)
    return _append(shapes, copies)


def rotate_copies(shapes, indices, count):
    """Studio's Rotate One/Two/Three/Five Copies: copies turned evenly about the centre."""
    centre = _centre(shapes, indices)
    original = tuple(shapes[i] for i in sorted(indices))
    copies = ()
    for k in range(1, count + 1):
        copies += model.rotate_selection(original, range(len(original)), 360 * k / (count + 1), center=centre)
    return _append(shapes, copies)


def replicate_direction(shapes, indices, copies, gap, angle):
    """Studio's Replicate advanced options: copies in any direction, `gap` apart."""
    if copies < 1:
        return shapes, ()
    box = _box(shapes, indices)
    radians = math.radians(angle)
    ux, uy = math.cos(radians), math.sin(radians)
    extent = abs(box.w * ux) + abs(box.h * uy)
    original = tuple(shapes[i] for i in sorted(indices))
    made = ()
    for k in range(1, copies + 1):
        made += model.move(original, range(len(original)), ux * (extent + gap) * k, uy * (extent + gap) * k)
    return _append(shapes, made)


def fill_page(shapes, indices, area, gap=0.0):
    """Studio's Fill Page: as many copies as fit in `area` (x, y, w, h), starting
    with the original moved into the first place."""
    box = _box(shapes, indices)
    ax, ay, aw, ah = area
    columns = int((aw + gap + 1e-9) // (box.w + gap)) if box.w + gap > 0 else 0
    rows = int((ah + gap + 1e-9) // (box.h + gap)) if box.h + gap > 0 else 0
    if columns < 1 or rows < 1:
        raise ValueError("The selection is larger than the cutting area.")
    moved = model.move(shapes, indices, ax - box.x, ay - box.y)
    original = tuple(moved[i] for i in sorted(indices))
    copies = ()
    for row in range(rows):
        for column in range(columns):
            if row or column:
                copies += model.move(original, range(len(original)), column * (box.w + gap), row * (box.h + gap))
    return _append(moved, copies)


def along_path(shapes, indices, path_index, count, *, rotate=True):
    """Studio's Object to Path: `count` copies of the selection spaced evenly
    along another shape's line, turned to follow it."""
    if count < 1:
        raise ValueError("Place at least one copy.")
    carried = [i for i in indices if i != path_index]
    if not carried:
        raise ValueError("Select the object to place, then the path to place it on.")
    line = shapes[path_index].paths()[0]
    points = list(line.points) + ([line.points[0]] if line.closed else [])
    lengths = [0.0]
    for a, b in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))
    total = lengths[-1]
    if total <= 0:
        raise ValueError("The path has no length.")
    centre = _centre(shapes, carried)
    original = tuple(shapes[i] for i in sorted(carried))
    made = ()
    spacing = total / count if line.closed else total / max(1, count - 1)
    for k in range(count):
        target = min(total, k * spacing)
        segment = max(i for i, length in enumerate(lengths) if length <= target) if target < total else len(points) - 2
        segment = min(segment, len(points) - 2)
        a, b = points[segment], points[segment + 1]
        span = math.dist(a, b) or 1
        t = (target - lengths[segment]) / span
        at = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        placed = model.move(original, range(len(original)), at[0] - centre[0], at[1] - centre[1])
        if rotate:
            angle = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
            if angle % 360:
                placed = model.rotate_selection(placed, range(len(placed)), angle, center=at)
        made += placed
    return _append(shapes, made)


# ---- Nesting ----------------------------------------------------------------------

def nest(shapes, indices, area, *, padding=1.0, rotations=4):
    """Studio's Nesting: pack the selection into `area` (x, y, w, h), trying
    `rotations` evenly spaced turns of each object. Objects are packed by their
    bounding boxes, largest first, each at the lowest then leftmost place it
    fits. Returns (shapes, placed indices, indices that did not fit)."""
    ax, ay, aw, ah = area
    units = list(model.selected_units(shapes, indices).values())
    if not units:
        raise ValueError("Select the objects to nest.")
    angles = tuple(360 * k / max(1, rotations) for k in range(max(1, rotations)))
    out = list(shapes)
    placed_boxes = []
    placed, left_out = [], []
    order = sorted(units, key=lambda unit: -(_box(shapes, unit).w * _box(shapes, unit).h))
    for unit in order:
        best = None
        for angle in angles:
            turned = model.rotate_selection(tuple(out[i] for i in unit), range(len(unit)), angle) if angle else \
                tuple(out[i] for i in unit)
            box = model.bounds(turned)
            w, h = box.w + padding, box.h + padding
            xs = sorted({ax} | {px + pw for px, py, pw, ph in placed_boxes})
            ys = sorted({ay} | {py + ph for px, py, pw, ph in placed_boxes})
            for y in ys:
                for x in xs:
                    if x + w > ax + aw + 1e-6 or y + h > ay + ah + 1e-6:
                        continue
                    if any(x < px + pw - 1e-6 and px < x + w - 1e-6 and y < py + ph - 1e-6 and py < y + h - 1e-6
                           for px, py, pw, ph in placed_boxes):
                        continue
                    if best is None or (y, x) < (best[0], best[1]):
                        best = (y, x, w, h, angle, turned, box)
                    break
        if best is None:
            left_out.extend(unit)
            continue
        y, x, w, h, angle, turned, box = best
        moved = model.move(turned, range(len(turned)), x + padding / 2 - box.x, y + padding / 2 - box.y)
        for i, shape in zip(unit, moved):
            out[i] = shape
        placed_boxes.append((x, y, w, h))
        placed.extend(unit)
    return tuple(out), tuple(sorted(placed)), tuple(sorted(left_out))
