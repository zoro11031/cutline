"""Editable cutter shapes and versioned template projects in millimetres."""

from dataclasses import asdict, dataclass, replace
import json
import math
import re
import tempfile

from . import actions, geometry, graphtec, importer, regmarks, template, toolpath, vector_regions
from .sticker_assets import Sticker


KINDS = ("Rectangle", "Rounded rectangle", "Ellipse", "Line")


def shape_to_add(tool):
    """What Add Shape places: the chosen shape tool's shape, or a rectangle
    when the tool is not a shape tool, so the button always does something."""
    return tool if tool in KINDS else "Rectangle"


ADD_SHAPE_TIP = "Add the shape tool's shape, or a rectangle, at the centre of the page."
ACTIONS = ("Cut", "Score", "None")
LINE_STYLES = {"solid": "Solid", "dashed": "Dashed", "dotted": "Dotted"}
ALL_ACTIONS = "All actions"
REVIEW_ACTIONS = (*ACTIONS[:2], ALL_ACTIONS)
ALIGNMENTS = ("Align Left", "Center Horizontally", "Align Right",
              "Align Top", "Center Vertically", "Align Bottom",
              "Space Horizontally", "Space Vertically")
SHAPE_LIMIT = 1000
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
MULTI_CONTOUR_KINDS = ("Compound", "Text", "Sticker")
POINT_EDIT_KINDS = ("Line", "Path", "Compound")
PATH_OPERATIONS = ("Weld", "Subtract", "Subtract All", "Intersect", "Divide", "Crop",
                   "Make Compound Path", "Release Compound Path")


@dataclass(frozen=True)
class TextSpec:
    """Text as Silhouette Studio's Text Style panel describes it (manual chapter 05)."""

    value: str
    family: str
    size_mm: float
    bold: bool = False
    italic: bool = False
    align: str = "left"             # left, center or right, for wrapped or multi-line text
    char_spacing: float = 100.0     # percent; 100 is the font's own spacing
    line_spacing: float = 100.0     # percent
    wrap_mm: float = 0.0            # text box width; 0 keeps each line on one line
    path: tuple = ()                # Text to Path: the guide line's points on the page
    path_offset: float = 0.0        # mm above (+) or below (-) the guide line
    path_start: float = 0.0         # mm along the guide line where the text begins

    @property
    def styled(self):
        """Whether anything beyond the original font, size and text is used."""
        return self != TextSpec(self.value, self.family, self.size_mm)


TEXT_ALIGNMENTS = ("left", "center", "right")


def valid_text(spec):
    return (isinstance(spec.value, str) and 1 <= len(spec.value) <= 2048 and bool(spec.value.strip())
            and "\0" not in spec.value and isinstance(spec.family, str) and 1 <= len(spec.family) <= 200
            and "\0" not in spec.family and _number(spec.size_mm, 1, 100)
            and type(spec.bold) is bool and type(spec.italic) is bool and spec.align in TEXT_ALIGNMENTS
            and _number(spec.char_spacing, 10, 500) and _number(spec.line_spacing, 10, 500)
            and _number(spec.wrap_mm, 0, SHAPE_LIMIT) and _number(spec.path_offset, -100, 100)
            and _number(spec.path_start, 0, 10 * SHAPE_LIMIT) and isinstance(spec.path, tuple)
            and (not spec.path or (2 <= len(spec.path) <= 4096 and all(
                isinstance(p, tuple) and len(p) == 2 and all(_number(v) for v in p) for p in spec.path))))


@dataclass(frozen=True)
class Shape:
    kind: str
    x: float = 45
    y: float = 45
    width: float = 40
    height: float = 30
    radius: float = 3
    rotation: float = 0
    flip_x: bool = False
    flip_y: bool = False
    visible: bool = True
    action: str = "Cut"
    points: tuple[tuple[float, float], ...] = ()
    closed: bool = True
    stroke: str = ""
    fill: str = ""
    group: int | None = None
    holder: int | None = None
    line_style: str = "solid"
    line_width_mm: float = 0.0
    contours: tuple[tuple[tuple[float, float], ...], ...] = ()
    text: TextSpec | None = None
    sticker: Sticker | None = None
    layer: str = ""
    # Bezier handles for a Path, one entry per point: () for a plain corner,
    # or (in handle, out handle, smooth) in the same 0..1 frame as `points`
    # (handles may lie outside it). Empty for every other kind.
    handles: tuple = ()
    name: str = ""
    locked: bool = False

    def paths(self):
        if self.kind not in MULTI_CONTOUR_KINDS:
            return (self.path(),)
        return tuple(self._cut_path(tuple((self.x + x * self.width, self.y + y * self.height)
                                          for x, y in ring), closed=True) for ring in self.contours)

    def path(self):
        if self.kind in MULTI_CONTOUR_KINDS:
            raise ValueError("Use paths() to retrieve every contour of a compound object.")
        rect = geometry.Rect(self.x, self.y, self.width, self.height)
        if self.kind == "Path":
            points = self._flattened_points()
        elif self.kind == "Line":
            points = ((rect.x, rect.y), (rect.x2, rect.y2))
        elif self.kind == "Ellipse":
            rx, ry = rect.w / 2, rect.h / 2
            max_radius = max(rx, ry)
            count = 4 * template._arc_segments(max_radius)
            angles = (2 * math.pi * i / count for i in range(count))
            points = tuple((rect.x + rx * (1 + math.cos(a)),
                            rect.y + ry * (1 + math.sin(a))) for a in angles)
        else:
            radius = self.radius if self.kind == "Rounded rectangle" else 0
            points = template.rounded_rect_points(rect, radius)
        closed = self.closed if self.kind == "Path" else self.kind != "Line"
        return self._cut_path(points, closed)

    def local_point(self, point):
        """A page point in the shape's own unrotated, unflipped frame."""
        if not (self.rotation or self.flip_x or self.flip_y):
            return point
        cx, cy = self.x + self.width / 2, self.y + self.height / 2
        r = math.radians(-self.rotation)
        c, s = math.cos(r), math.sin(r)
        dx, dy = point[0] - cx, point[1] - cy
        lx, ly = c * dx - s * dy, s * dx + c * dy
        fx, fy = (-1 if self.flip_x else 1), (-1 if self.flip_y else 1)
        return cx + fx * lx, cy + fy * ly

    def _flattened_points(self):
        """A Path's points on the page, with any curved segments flattened."""
        place = lambda p: (self.x + p[0] * self.width, self.y + p[1] * self.height)  # noqa: E731
        anchors = [place(p) for p in self.points]
        if not self.handles:
            return tuple(anchors)
        count = len(anchors)
        out = [anchors[0]]
        segments = count if self.closed else count - 1
        for i in range(segments):
            j = (i + 1) % count
            a, b = anchors[i], anchors[j]
            first, second = self.handles[i], self.handles[j]
            c1 = place(first[1]) if first else a
            c2 = place(second[0]) if second else b
            if c1 == a and c2 == b:
                out.append(b)
                continue
            steps = max(8, min(64, round((math.dist(a, c1) + math.dist(c1, c2) + math.dist(c2, b)) / 0.4)))
            for step in range(1, steps + 1):
                t = step / steps
                u = 1 - t
                out.append(tuple(u ** 3 * a[k] + 3 * u * u * t * c1[k] + 3 * u * t * t * c2[k] + t ** 3 * b[k]
                                 for k in (0, 1)))
        if self.closed and len(out) > 1 and out[-1] == out[0]:
            out.pop()
        return tuple(out)

    def frame(self):
        """Transformed image frame in top-left, top-right, bottom-right, bottom-left order."""
        x, y, w, h = self.x, self.y, self.width, self.height
        return self._cut_path(((x, y), (x + w, y), (x + w, y + h), (x, y + h)), True)

    def _cut_path(self, points, closed):
        if self.rotation or self.flip_x or self.flip_y:
            cx, cy = self.x + self.width/2, self.y + self.height/2
            r = math.radians(self.rotation)
            c, s = math.cos(r), math.sin(r)
            fx, fy = (-1 if self.flip_x else 1), (-1 if self.flip_y else 1)
            points = tuple((cx+c*fx*(x-cx)-s*fy*(y-cy), cy+s*fx*(x-cx)+c*fy*(y-cy))
                           for x, y in points)
        return template.CutPath(points, closed=closed,
                                label=f"{self.kind}|{self.stroke}" if self.stroke else self.kind,
                                fill=self.fill)


def bounds(shapes):
    """Bounds of the actual transformed paths."""
    xs, ys = zip(*(point for shape in shapes for path in _selection_paths(shape) for point in path.points))
    return geometry.Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def hit(shape, point, tolerance):
    paths = _selection_paths(shape)
    closed_edges = [edge for path in paths if path.closed for edge in toolpath._edges(path.points, True)]
    if toolpath._inside(point, closed_edges):
        return True
    open_edges = [edge for path in paths if not path.closed for edge in toolpath._edges(path.points, False)]
    for a, b in (*closed_edges, *open_edges):
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx*dx + dy*dy) if a != b else 0
        t = max(0, min(1, t))
        if math.dist(point, (a[0] + t*dx, a[1] + t*dy)) <= tolerance:
            return True
    return False


def _selection_paths(shape):
    return (shape.frame(),) if shape.sticker else shape.paths()


def move(shapes, indices, dx, dy):
    selected = [shapes[i] for i in indices]
    if not selected:
        return shapes
    dx = max(-SHAPE_LIMIT - min(s.x for s in selected), min(dx, SHAPE_LIMIT - max(s.x for s in selected)))
    dy = max(-SHAPE_LIMIT - min(s.y for s in selected), min(dy, SHAPE_LIMIT - max(s.y for s in selected)))
    return tuple(replace(shape, x=shape.x + dx, y=shape.y + dy) if i in indices else shape
                 for i, shape in enumerate(shapes))


def snap_move(shapes, indices, dx, dy, width, height, tolerance, guides=()):
    selection = bounds(shapes[i] for i in indices)
    others = [bounds((shape,)) for i, shape in enumerate(shapes) if shape.visible and i not in indices]
    targets = ([0, width / 2, width], [0, height / 2, height])
    for orient, pos in guides:
        if orient == "v":
            targets[0].append(pos)
        elif orient == "h":
            targets[1].append(pos)
    for box in others:
        targets[0].extend((box.x, box.x + box.w / 2, box.x2))
        targets[1].extend((box.y, box.y + box.h / 2, box.y2))
    moves = []
    for axis, size, delta, candidates in zip(("x", "y"), ("w", "h"), (dx, dy), targets):
        start, length = getattr(selection, axis) + delta, getattr(selection, size)
        shift = min((guide - (start + length * anchor) for guide in candidates for anchor in (0, 0.5, 1)), key=abs)
        moves.append(delta + shift if abs(shift) <= tolerance else delta)
    return tuple(moves)


def _place_center(shape, x, y, **changes):
    x, y = x - shape.width / 2, y - shape.height / 2
    if not _number(x) or not _number(y):
        raise ValueError("This move would place a shape too far from the page.")
    return replace(shape, x=x, y=y, **changes)


def rotate_page(project, clockwise):
    shapes = []
    for shape in project.shapes:
        x, y = shape.x + shape.width / 2, shape.y + shape.height / 2
        center = (project.height - y, x) if clockwise else (y, project.width - x)
        rotation = (shape.rotation + (90 if clockwise else -90)) % 360
        shapes.append(_place_center(shape, *center, rotation=rotation))
    return replace(project, width=project.height, height=project.width, shapes=tuple(shapes))


def transform_selection(shapes, indices, operation):
    if operation in ("left", "right"):
        return rotate_selection(shapes, indices, -90 if operation == "left" else 90)
    if not indices:
        return shapes
    area = bounds(shapes[i] for i in indices)
    cx, cy = area.x + area.w / 2, area.y + area.h / 2
    result = list(shapes)
    for index in indices:
        shape = shapes[index]
        x, y = shape.x + shape.width / 2, shape.y + shape.height / 2
        if operation == "horizontal":
            result[index] = _place_center(shape, 2 * cx - x, y,
                                          rotation=-shape.rotation, flip_x=not shape.flip_x)
        elif operation == "vertical":
            result[index] = _place_center(shape, x, 2 * cy - y,
                                          rotation=-shape.rotation, flip_y=not shape.flip_y)
    return tuple(result)


def rotate_point(point, center, angle):
    """Rotate paper coordinates clockwise around a fixed center."""
    radians = math.radians(angle)
    c, s = round(math.cos(radians), 15), round(math.sin(radians), 15)
    cx, cy = center
    dx, dy = point[0] - cx, point[1] - cy
    return cx + c * dx - s * dy, cy + s * dx + c * dy


def rotate_selection(shapes, indices, angle, *, center=None):
    if not indices or not angle % 360:
        return shapes
    if center is None:
        area = bounds(shapes[i] for i in indices)
        center = (area.x + area.w / 2, area.y + area.h / 2)
    result = list(shapes)
    for index in indices:
        shape = shapes[index]
        point = (shape.x + shape.width / 2, shape.y + shape.height / 2)
        result[index] = _place_center(shape, *rotate_point(point, center, angle),
                                      rotation=(shape.rotation + angle) % 360)
    return tuple(result)


def _valid_scale_factor(factor):
    return math.isfinite(factor) and factor > 0


def _valid_scaled_size(kind, width, height):
    return max(abs(width), abs(height)) >= 0.1 and (
        kind in ("Line", "Path") or min(width, height) >= 0.1
    )


def scale_selection(shapes, indices, anchor, factor):
    """Scale selected objects proportionally about a fixed paper coordinate."""
    if not indices or factor == 1:
        return shapes
    if not _valid_scale_factor(factor):
        raise ValueError("Drag away from the opposite corner to scale.")
    cx, cy = anchor
    result = list(shapes)
    for index in indices:
        shape = shapes[index]
        x, y = cx + (shape.x - cx) * factor, cy + (shape.y - cy) * factor
        width, height = shape.width * factor, shape.height * factor
        radius = shape.radius * factor if shape.kind == "Rounded rectangle" else shape.radius
        if not all(_number(v) for v in (x, y, width, height, radius)):
            raise ValueError("This scale would place a shape outside the editable size or coordinate range.")
        if not _valid_scaled_size(shape.kind, width, height):
            raise ValueError("The shape is too small to scale further.")
        result[index] = replace(shape, x=x, y=y, width=width, height=height, radius=radius)
    return tuple(result)


def edit_selection_bounds(shapes, selection, field, value):
    """Move the selection or scale it proportionally about its top-left."""
    area = bounds(shapes[i] for i in selection)
    if field == "x":
        return move(shapes, selection, value - area.x, 0)
    if field == "y":
        return move(shapes, selection, 0, value - area.y)
    current = area.w if field == "width" else area.h
    if current <= 0:
        raise ValueError("The selection has no size in that direction to scale.")
    return scale_selection(shapes, selection, (area.x, area.y), value / current)


def matching_color(shapes, indices, field):
    colors = {getattr(shapes[index], field) for index in indices}
    return tuple(i for i, shape in enumerate(shapes) if shape.visible and getattr(shape, field) in colors)


def convert_path(shape, simplify=False):
    if shape.sticker:
        if not shape.contours:
            raise ValueError("Trace the sticker before converting its outlines to paths.")
        if simplify:
            raise ValueError("Convert the sticker’s outlines to paths before simplifying them.")
    paths = tuple(_simplify_path(path) if simplify else path for path in shape.paths())
    return styled_contours(paths, shape)


def _simplify_path(path):
    points = path.points
    if len(points) > (3 if path.closed else 2):
        if path.closed:
            split = max(range(1, len(points)), key=lambda i: math.dist(points[0], points[i]))
            first = _simplify_line(points[:split + 1], 0.1)
            last = _simplify_line((*points[split:], points[0]), 0.1)
            reduced = (*first[:-1], *last[:-1])
            if len(reduced) >= 3:
                points = reduced
        else:
            points = _simplify_line(points, 0.1)
    return replace(path, points=points)


def offset_shape(shape, distance, join="corner"):
    if not math.isfinite(distance) or not distance:
        raise ValueError("Enter a nonzero offset distance.")
    result = vector_regions.modify("Offset", (_closed_contours(shape),), distance=distance, join=join)
    if not result:
        raise ValueError("This inset is too large and leaves no cut area.")
    return replace(styled_contours(tuple(template.CutPath(ring, True) for ring in result), shape), group=None)


def slice_shape(shape, p1, p2):
    """Slice a closed shape with a line from p1 to p2 into multiple shapes."""
    contours = _closed_contours(shape)
    pieces = vector_regions.slice_contours(contours, p1, p2)
    if not pieces:
        return (shape,)
    result = []
    for piece in pieces:
        paths = tuple(template.CutPath(ring, True) for ring in piece)
        result.append(replace(styled_contours(paths, shape, compound=len(piece) > 1), group=None))
    return tuple(result)


def _closed_contours(shape):
    paths = shape.paths()
    if not paths:
        raise ValueError("Trace the sticker before working with its cut outline.")
    if any(not path.closed for path in paths):
        raise ValueError("Choose a closed shape. Open lines have no filled area.")
    return tuple(path.points for path in paths)


def modify_selection(shapes, indices, operation):
    indices = tuple(sorted(indices))
    if operation == "Release Compound Path":
        if len(indices) != 1 or shapes[indices[0]].kind != "Compound":
            raise ValueError("Select one compound path to release its contours.")
        start = indices[0]
        shape = shapes[start]
        replacement = tuple(styled_contours((path,), shape) for path in shape.paths())
    else:
        if len(indices) < 2:
            raise ValueError(f"Select at least two closed objects for {operation.lower()}.")
        start = indices[0]
        if any(shapes[i].sticker for i in indices):
            raise ValueError("Convert sticker outlines to paths before combining them with other objects.")
        selected = tuple(_closed_contours(shapes[i]) for i in indices)
        if operation in ("Divide", "Subtract All", "Crop"):
            return _split_operation(shapes, indices, operation, selected)
        if operation == "Make Compound Path":
            rings = vector_regions.outlines(tuple(ring for contours in selected for ring in contours), nonzero=False)
        else:
            rings = vector_regions.modify(operation, selected)
        if not rings:
            raise ValueError("This operation leaves no cut area. Adjust the selected objects.")
        result = styled_contours(tuple(template.CutPath(ring, True) for ring in rings), shapes[start],
                                 compound=operation == "Make Compound Path")
        replacement = (replace(result, group=None),)
    remaining = tuple(shape for i, shape in enumerate(shapes) if i not in indices)
    return remaining[:start] + replacement + remaining[start:], range(start, start + len(replacement))


def _pieces(polygons, style):
    """Polygons as shapes in a source shape's style: one ring a path, more a compound."""
    out = []
    for rings in polygons:
        rings = tuple(ring for ring in rings if len(set(ring)) >= 3)
        if rings:
            paths = tuple(template.CutPath(ring, True) for ring in rings)
            out.append(replace(styled_contours(paths, style, compound=len(rings) > 1), group=None))
    return out


def _split_operation(shapes, indices, operation, selected):
    if operation == "Divide":
        if len(indices) > 8:
            raise ValueError("Divide works on up to eight objects at once.")
        replacement = []
        for owner, polygons in vector_regions.divide(selected):
            replacement.extend(_pieces(polygons, shapes[indices[owner]]))
    elif operation == "Crop":
        replacement = _pieces(vector_regions.crop(selected), shapes[indices[0]])
    else:
        results = vector_regions.subtract_all(selected)
        replacement = [piece for index, polygons in zip(indices, results) for piece in _pieces(polygons, shapes[index])]
    if not replacement:
        raise ValueError("This operation leaves no cut area. Adjust the selected objects.")
    start = indices[0]
    remaining = tuple(shape for i, shape in enumerate(shapes) if i not in indices)
    return remaining[:start] + tuple(replacement) + remaining[start:], range(start, start + len(replacement))


def detach_lines(shapes, indices):
    """Studio's Detach Lines: a filled shape becomes its fill (printed, not cut)
    and its outline (cut), as two objects."""
    filled = [i for i in indices if shapes[i].fill and shapes[i].closed]
    if not filled:
        raise ValueError("Detach Lines needs a selected shape with a fill.")
    out, made = [], []
    for index, shape in enumerate(shapes):
        if index in filled:
            out.append(replace(shape, stroke="", line_width_mm=0.0, action="None", group=None))
            made.append(len(out))
            out.append(replace(shape, fill="", group=None))
        else:
            out.append(shape)
    return tuple(out), tuple(made)


def simplified_points(points, tolerance, *, closed=False):
    """Douglas-Peucker on a drawn line; a closed one keeps at least three points."""
    points = tuple(points)
    if len(points) < 3:
        return points
    if closed:
        split = max(range(1, len(points)), key=lambda i: math.dist(points[0], points[i]))
        first = _simplify_line(points[:split + 1], tolerance)
        second = _simplify_line(points[split:] + points[:1], tolerance)
        return tuple(first[:-1] + second[:-1])
    return tuple(_simplify_line(points, tolerance))


def _simplify_line(points, tolerance):
    keep = {0, len(points) - 1}
    work = [(0, len(points) - 1)]
    while work:
        start, end = work.pop()
        if end <= start + 1:
            continue
        a, b = points[start], points[end]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length_sq = dx * dx + dy * dy
        split, distance = start, tolerance
        for index in range(start + 1, end):
            x, y = points[index]
            along = ((x - a[0]) * dx + (y - a[1]) * dy) / length_sq if length_sq else 0
            along = min(1, max(0, along))
            error = math.hypot(x - a[0] - along * dx, y - a[1] - along * dy)
            if error > distance:
                split, distance = index, error
        if split != start:
            keep.add(split)
            work.extend(((start, split), (split, end)))
    return tuple(points[index] for index in sorted(keep))


def reorder_objects(shapes, indices, destination):
    """Insert selected objects at a boundary in the original list."""
    selected = tuple(sorted(set(indices)))
    moving = set(selected)
    start = destination - sum(index < destination for index in selected)
    rest = tuple(shape for i, shape in enumerate(shapes) if i not in moving)
    start = min(len(rest), max(0, start))
    ordered = rest[:start] + tuple(shapes[i] for i in selected) + rest[start:]
    return ordered, tuple(range(start, start + len(selected)))


def selected_units(shapes, indices):
    units = {}
    for index in indices:
        group = shapes[index].group
        key = ("shape", index) if group is None else ("group", group)
        units.setdefault(key, []).append(index)
    return {unit[0]: unit for unit in units.values()}


def align(shapes, indices, operation):
    units = selected_units(shapes, indices)
    if len(units) < 2:
        return shapes
    boxes = {i: bounds(shapes[index] for index in unit) for i, unit in units.items()}
    selection = bounds(shapes[i] for i in indices)
    option = ALIGNMENTS.index(operation)
    horizontal = option < 3 or option == 6
    axis = "x" if horizontal else "y"
    size = "w" if horizontal else "h"
    starts = {i: getattr(box, axis) for i, box in boxes.items()}
    lengths = {i: getattr(box, size) for i, box in boxes.items()}
    origin, extent = getattr(selection, axis), getattr(selection, size)
    if option < 6:
        anchor = (option % 3) / 2
        targets = {i: origin + (extent - lengths[i]) * anchor for i in units}
    else:
        ordered = sorted(units, key=lambda i: (starts[i], i))
        extent = starts[ordered[-1]] + lengths[ordered[-1]] - starts[ordered[0]]
        position = starts[ordered[0]]
        gap = (extent - sum(lengths.values())) / (len(ordered) - 1)
        targets = {}
        for i in ordered:
            targets[i] = position
            position += lengths[i] + gap
    result = list(shapes)
    for unit, target in targets.items():
        for i in units[unit]:
            value = getattr(shapes[i], axis) + target - starts[unit]
            if not _number(value):
                raise ValueError("Alignment exceeds the editable coordinate range.")
            result[i] = replace(shapes[i], **{axis: value})
    return tuple(result)


def group_members(shapes, index):
    group = shapes[index].group
    return tuple(i for i, shape in enumerate(shapes) if shape.group == group) if group else (index,)


def group_selection(shapes, indices, *, release=False):
    if not indices or (len(indices) < 2 and not release):
        return shapes
    if not release and shapes[indices[0]].group is not None and len(selected_units(shapes, indices)) == 1:
        return shapes
    group = None if release else _next_group(shapes)
    selected = set(indices)
    existing = {shapes[i].group for i in indices if shapes[i].group is not None}
    return tuple(replace(shape, group=group) if i in selected or shape.group in existing else shape
                 for i, shape in enumerate(shapes))


def _next_group(shapes):
    return max((shape.group or 0 for shape in shapes), default=0) + 1


def _copy_selection(shapes, indices, offsets):
    base = tuple(shapes[i] for i in sorted(indices))
    copies = []
    next_group = _next_group(shapes)
    for dx, dy in offsets:
        groups = {}
        for shape in base:
            group = shape.group
            if group is not None:
                if group not in groups:
                    groups[group] = next_group
                    next_group += 1
                group = groups[group]
            copy = replace(shape, x=shape.x + dx, y=shape.y + dy, group=group)
            if not (_number(copy.x) and _number(copy.y)):
                raise ValueError("The copy exceeds the editable coordinate range.")
            copies.append(copy)
    return shapes + tuple(copies)


def duplicate(shapes, indices):
    return _copy_selection(shapes, indices, [(5, 5)])


def replicate(shapes, indices, columns, rows, gap_x, gap_y):
    if not indices or columns == rows == 1:
        return shapes
    box = bounds(shapes[i] for i in indices)
    offsets = ((col * (box.w + gap_x), row * (box.h + gap_y))
               for row in range(rows) for col in range(columns) if row or col)
    return _copy_selection(shapes, indices, offsets)


def stroke_color(value):
    if value == "":
        return value
    canonical = importer.normalise_colour(value)
    if not isinstance(canonical, str) or not _COLOR.fullmatch(canonical):
        raise ValueError("Enter a color such as #336699 or leave it blank.")
    return canonical.lower()


def artwork(shapes, width, height, action="Cut"):
    paths = []
    for shape in shapes:
        if not shape.visible or shape.action == "None":
            continue
        if action not in (shape.action, ALL_ACTIONS):
            continue
        base_layer = shape.layer.strip() if shape.layer.strip() else shape.action
        layer_tuple = ((base_layer, actions.HOLDER_SETTINGS[shape.holder])
                       if shape.holder else (base_layer,))
        for path in shape.paths():
            paths.append(replace(path, layer=layer_tuple))
    return importer.Imported(tuple(paths), width, height,
                             "Template editor", "editor coordinates in millimetres",
                             page_size_mm=(width, height), use_document_page=True)


def holder_defaults(drawing):
    labels = set(actions.HOLDER_SETTINGS.values())
    groups = actions.group_paths(drawing.paths, "layer")
    if len(groups) > 1 or any(key.rsplit(" / ", 1)[-1] in labels for key in groups):
        return {key: (True, label if (label := key.rsplit(" / ", 1)[-1]) in labels else actions.CURRENT_SETTINGS,
                      None, position)
                for position, key in enumerate(groups, 1)}
    return {}

@dataclass(frozen=True)
class Registration:
    inset_mm: float
    length_mm: float
    thickness_mm: float
    pattern: str

    @classmethod
    def from_marks(cls, marks):
        return cls(marks.inset_mm, marks.length_mm, marks.thickness_mm, marks.pattern)

    def on_page(self, width, height):
        return regmarks.marks(width, height, self.inset_mm, self.length_mm,
                              self.thickness_mm, self.pattern)


@dataclass(frozen=True)
class Project:
    width: float
    height: float
    shapes: tuple[Shape, ...] = ()
    registration: Registration | None = None
    mat: str = "auto"
    guides: tuple[tuple[str, float], ...] = ()

    def artwork(self, action="Cut"):
        return artwork(self.shapes, self.width, self.height, action)


def send_action(shapes):
    """The actions a design carries to Send: Cut, Score, or both."""
    present = {shape.action for shape in shapes if shape.visible and shape.action in ACTIONS[:2]}
    if len(present) > 1:
        return ALL_ACTIONS
    return present.pop() if present else None


def send_handoff(project, origin, applied):
    """What switching to Send does with the design, as Silhouette Studio's
    SEND tab does: Send cuts the design. A design left as it was opened, or
    already in Send, changes nothing. If the edited design supplied Send's
    current job, removing its last cut path blocks the handoff.

    Returns (verdict, action): "apply", "keep", "empty", "stickers" (those
    go through Export Print & Cut) or "invalid" (refused, with the reason
    as the second item)."""
    if project == applied or (applied is None and project == origin):
        return "keep", None
    if any(shape.visible and shape.sticker for shape in project.shapes):
        return "stickers", None
    action = send_action(project.shapes)
    if action is None:
        if applied is not None:
            return "invalid", "No cut paths in this design. Add a cut path or undo the last edit."
        return "empty", None
    try:
        drawing = project.artwork(action)
    except ValueError as error:
        return "invalid", str(error)
    if not drawing.paths or any(path.length_mm() <= 0 for path in drawing.paths):
        return "invalid", "Draw or resize the paths before sending."
    return "apply", action


class History:
    def __init__(self, project):
        self.current = project
        self.past = []
        self.future = []

    def commit(self, project):
        if project != self.current:
            self.past.append(self.current)
            self.current = project
            self.future.clear()

    def undo(self):
        if self.past:
            self.future.append(self.current)
            self.current = self.past.pop()

    def redo(self):
        if self.future:
            self.past.append(self.current)
            self.current = self.future.pop()


def project_version(project):
    """The lowest format version that can hold every feature the project uses."""
    version = 2
    if any(shape.kind in MULTI_CONTOUR_KINDS for shape in project.shapes):
        version = 3
    if any(shape.sticker for shape in project.shapes):
        version = 4
    if any(shape.layer for shape in project.shapes) or project.guides:
        version = 5
    if any(shape.handles or (shape.text and shape.text.styled) for shape in project.shapes):
        version = 6
    if any(shape.name or shape.locked for shape in project.shapes):
        version = 7
    return version


def project_document(project):
    objects = []
    for shape in project.shapes:
        fields = asdict(shape)
        for name, default in (("holder", None), ("line_style", "solid"), ("line_width_mm", 0.0),
                              ("text", None), ("contours", ()), ("sticker", None), ("layer", ""),
                              ("handles", ()), ("name", ""), ("locked", False)):
            if fields[name] == default:
                del fields[name]
        objects.append(fields)
    document = {"version": project_version(project), "page_mm": [project.width, project.height],
                "registration": asdict(project.registration) if project.registration else None,
                "mat": project.mat, "objects": objects}
    if project.guides:
        document["guides"] = [list(g) for g in project.guides]
    return document


def save_project(path, project):
    document = project_document(project)
    content = json.dumps(document, indent=2, allow_nan=False) + "\n"
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, encoding="utf-8", mode="w") as f:
            temporary = type(path)(f.name)
            f.write(content)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_project(path, shapes, width, height):
    save_project(path, Project(width, height, tuple(shapes)))


def _number(value, low=-SHAPE_LIMIT, high=SHAPE_LIMIT):
    return type(value) in (int, float) and low <= value <= high


def import_shapes(path):
    return editable_import(importer.load(path))


def editable_import(drawing):
    omissions = [w for w in drawing.warnings if w.startswith((
        "ignored ", "the file uses CSS", "Excluded Inkscape layers"))]
    if omissions:
        raise ValueError("\n".join(omissions))
    return shapes_from_paths(drawing.paths), "\n".join([drawing.scale_note, *drawing.warnings])


def shape_from_path(contour):
    xs, ys = zip(*contour.points)
    x, y = min(xs), min(ys)
    w, h = max(xs)-x, max(ys)-y
    if not all(_number(value) for value in (x, y, w, h)):
        raise ValueError("The vector positions are outside the design workspace's editable range.")
    points = tuple(((u-x)/(w or 1), (v-y)/(h or 1)) for u, v in contour.points)
    return Shape("Path", x, y, w, h, points=points, closed=contour.closed,
                 stroke=contour.stroke if _COLOR.fullmatch(contour.stroke) else "",
                 fill=contour.fill if _COLOR.fullmatch(contour.fill) else "")


def shapes_from_paths(paths):
    return tuple(shape_from_path(contour) for contour in paths)


def styled_contours(paths, original, *, compound=False):
    if len(paths) == 1 and not compound:
        converted = shape_from_path(paths[0])
    else:
        converted = compound_shape(tuple(path.points for path in paths))
    return replace(converted, stroke=original.stroke, fill=original.fill, visible=original.visible,
                   action=original.action, group=original.group, holder=original.holder,
                   line_style=original.line_style, line_width_mm=original.line_width_mm,
                   layer=original.layer, name=original.name, locked=original.locked)


def compound_shape(rings, *, text=None):
    if not rings or any(len(set(ring)) < 3 for ring in rings):
        raise ValueError("The object needs at least one closed contour with an area.")
    xs, ys = zip(*(point for ring in rings for point in ring))
    x, y = min(xs), min(ys)
    w, h = max(xs) - x, max(ys) - y
    if not _number(x) or not _number(y) or not all(_number(n, 0.1) for n in (w, h)):
        raise ValueError("The contours are outside the editable range or smaller than 0.1 mm.")
    normalized = tuple(tuple(((u-x)/w, (v-y)/h) for u, v in ring) for ring in rings)
    return Shape("Text" if text else "Compound", x, y, w, h, contours=normalized, text=text)


def nearest_vertex(shape, pointer):
    return min((math.dist(pointer, point), (i, j)) for i, path in enumerate(shape.paths())
               for j, point in enumerate(path.points))


def move_vertex(shape, index, position):
    contour, vertex = (0, index) if isinstance(index, int) else index
    paths = list(shape.paths())
    path = paths[contour]
    paths[contour] = replace(path, points=path.points[:vertex] + (position,) + path.points[vertex+1:])
    return styled_contours(paths, shape)


def insert_vertex(shape, position, tolerance):
    """Add a point to the outline edge nearest *position*, as Studio's edit
    points do when a template outline needs more detail."""
    if shape.kind not in POINT_EDIT_KINDS:
        raise ValueError("Convert the object to a path before adding points.")
    best = None
    paths = list(shape.paths())
    for contour, path in enumerate(paths):
        points = path.points
        count = len(points) if path.closed else len(points) - 1
        for i in range(count):
            a, b = points[i], points[(i + 1) % len(points)]
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = dx * dx + dy * dy
            t = 0 if not length else max(0, min(1, ((position[0] - a[0]) * dx + (position[1] - a[1]) * dy) / length))
            near = (a[0] + t * dx, a[1] + t * dy)
            distance = math.dist(near, position)
            if 0 < t < 1 and (best is None or distance < best[0]):
                best = (distance, contour, i + 1, near)
    if best is None or best[0] > tolerance:
        raise ValueError("Double-click on the outline to add a point.")
    _distance, contour, vertex, near = best
    path = paths[contour]
    paths[contour] = replace(path, points=path.points[:vertex] + (near,) + path.points[vertex:])
    return styled_contours(paths, shape), (contour, vertex)


def smooth_points(points, *, closed=True, steps=10):
    """A Catmull-Rom curve through every placed point, flattened to a polyline.

    The Curve tool's outline passes through each click, so a template traced
    around a blank lands where the user clicked, between them it bends."""
    points = tuple(points)
    if len(points) < 3:
        return points
    count = len(points)
    if closed:
        at = lambda i: points[i % count]  # noqa: E731
        segments = range(count)
    else:
        at = lambda i: points[max(0, min(count - 1, i))]  # noqa: E731
        segments = range(count - 1)
    curve = []
    for i in segments:
        p0, p1, p2, p3 = at(i - 1), at(i), at(i + 1), at(i + 2)
        for step in range(steps):
            t = step / steps
            t2, t3 = t * t, t * t * t
            curve.append(tuple(0.5 * (2 * p1[k] + (p2[k] - p0[k]) * t + (2 * p0[k] - 5 * p1[k] + 4 * p2[k] - p3[k]) * t2
                                      + (3 * p1[k] - p0[k] - 3 * p2[k] + p3[k]) * t3) for k in (0, 1)))
    if not closed:
        curve.append(points[-1])
    return tuple(curve)


def paste(shapes, clipboard, *, offset=False):
    """Paste copied objects, giving their groups fresh numbers."""
    if offset and clipboard:
        clipboard = move(clipboard, range(len(clipboard)), bounds(clipboard).w + 2, 0)
    groups = {}
    next_group = _next_group(shapes)
    pasted = []
    for shape in clipboard:
        group = shape.group
        if group is not None:
            group = groups.setdefault(group, next_group + len(groups))
        pasted.append(replace(shape, group=group))
    return tuple(shapes) + tuple(pasted)


def erase(shapes, indices, stroke, radius):
    """Erase a brush stroke from the chosen shapes. A closed shape keeps its
    style in every piece left; a piece with holes becomes a compound path."""
    chosen = [i for i in indices if shapes[i].visible and not shapes[i].sticker and not shapes[i].locked]
    inputs = [(all(path.closed for path in shapes[i].paths()), tuple(path.points for path in shapes[i].paths()))
              for i in chosen]
    results = dict(zip(chosen, vector_regions.erase(inputs, stroke, radius)))
    out, changed = [], False
    for index, shape in enumerate(shapes):
        result = results.get(index)
        if result is None:
            out.append(shape)
            continue
        changed = True
        closed = all(path.closed for path in shape.paths())
        for piece in result:
            if closed:
                paths = tuple(template.CutPath(ring, True) for ring in piece)
                out.append(replace(styled_contours(paths, shape, compound=len(piece) > 1), group=shape.group))
            elif len(set(piece)) >= 2:
                out.append(replace(styled_contours((template.CutPath(piece, False),), shape), group=shape.group))
    return tuple(out), changed


def _valid_handles(handles, count):
    """Stored handles as tuples, or None if they are not a handle per point."""
    if not isinstance(handles, (list, tuple)) or len(handles) != count:
        return None
    out = []
    for entry in handles:
        if not entry:
            out.append(())
            continue
        if not isinstance(entry, (list, tuple)) or len(entry) != 3 or type(entry[2]) is not bool:
            return None
        pair = tuple(tuple(point) for point in entry[:2])
        if not all(len(point) == 2 and all(_number(v, -10, 11) for v in point) for point in pair):
            return None
        out.append((*pair, entry[2]))
    return tuple(out)


def load_project(path, legacy_registration=None):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") not in (1, 2, 3, 4, 5, 6, 7):
        raise ValueError("Unsupported template project version.")
    page = document.get("page_mm")
    if not isinstance(page, list) or len(page) != 2 or not all(_number(v, 1) for v in page):
        raise ValueError("Invalid project page size.")
    legacy = document['version'] == 1
    registration = legacy_registration if legacy else None
    if not legacy and document.get('registration') is not None:
        try:
            registration = Registration(**document['registration'])
            if (registration.pattern not in regmarks.PATTERNS or
                    not _number(registration.inset_mm, 2, 40) or
                    not _number(registration.length_mm, regmarks.MIN_LENGTH_MM, regmarks.MAX_LENGTH_MM) or
                    not _number(registration.thickness_mm, regmarks.MIN_THICKNESS_MM, regmarks.MAX_THICKNESS_MM)):
                raise ValueError('Invalid project registration settings.')
        except TypeError as error:
            raise ValueError('Invalid project registration settings.') from error
    mat = document.get("mat", "auto")
    if not isinstance(mat, str) or mat not in graphtec.MAT_OPTIONS:
        raise ValueError("Invalid project cutting mat.")
    # Older releases could emit guides and layers in v4 projects with stickers.
    if document["version"] < 4 and "guides" in document:
        raise ValueError("Guides need template project version 5.")
    guides_data = document.get("guides", [])
    if not isinstance(guides_data, list) or any(
            not isinstance(guide, list) or len(guide) != 2 or guide[0] not in ("h", "v") or not _number(guide[1])
            for guide in guides_data):
        raise ValueError("Invalid template guides.")
    guides = tuple((axis, float(position)) for axis, position in guides_data)
    shapes = document.get("shapes" if legacy else "objects")
    if not isinstance(shapes, list):
        raise ValueError("Missing template shapes.")
    result = []
    for item in shapes:
        try:
            fields = {"kind", "x", "y", "width", "height", "radius"} if legacy else Shape.__dataclass_fields__.keys()
            required = fields if legacy else fields - {"stroke", "fill", "group", "holder", "line_style",
                                                        "line_width_mm", "contours", "text", "sticker", "layer",
                                                        "handles", "name", "locked"}
            if not isinstance(item, dict) or not required <= item.keys() <= fields:
                raise TypeError("incomplete shape")
            shape = Shape(**item)
            numbers = (shape.x, shape.y, shape.width, shape.height, shape.radius)
            kinds = KINDS if legacy else (*KINDS, "Path")
            if document["version"] >= 3:
                kinds += ("Compound", "Text")
            if document["version"] >= 4:
                kinds += ("Sticker",)
            valid = (shape.kind in kinds and
                     all(_number(v) for v in numbers) and shape.radius >= 0 and
                     _number(shape.rotation, -360, 360) and shape.action in ACTIONS and
                     (shape.holder is None or (type(shape.holder) is int and shape.holder in (1, 2))) and
                     shape.line_style in LINE_STYLES and _number(shape.line_width_mm, 0, 5) and
                     (shape.group is None or (type(shape.group) is int and shape.group > 0)) and
                     isinstance(shape.layer, str) and len(shape.layer) <= 100 and "\0" not in shape.layer and
                     (not shape.layer or document["version"] >= 4) and
                     isinstance(shape.name, str) and len(shape.name) <= 100 and "\0" not in shape.name and
                     (not shape.name or document["version"] >= 7) and
                     type(shape.locked) is bool and (not shape.locked or document["version"] >= 7) and
                     all(type(v) is bool for v in (shape.closed, shape.visible, shape.flip_x, shape.flip_y)))
            shape = replace(shape, stroke=stroke_color(shape.stroke), fill=stroke_color(shape.fill))
            if shape.kind == "Path":
                points = tuple(tuple(p) for p in shape.points)
                valid = (valid and len(points) >= 2 and min(shape.width, shape.height) >= 0 and
                         all(len(p) == 2 and all(_number(v, 0, 1) for v in p) for p in points))
                handles = _valid_handles(shape.handles, len(points)) if shape.handles else ()
                valid = valid and (not shape.handles or (handles is not None and document["version"] >= 6))
                shape = replace(shape, points=points, handles=handles or ())
            else:
                valid = (valid and not shape.points and not shape.handles and
                         (shape.kind == "Line" or min(shape.width, shape.height) >= 0.1))
                shape = replace(shape, points=())
            if shape.kind in MULTI_CONTOUR_KINDS:
                rings = tuple(tuple(tuple(point) for point in ring) for ring in shape.contours)
                valid = (valid and shape.closed and (bool(rings) or shape.kind == "Sticker") and all(
                    len(set(ring)) >= 3 and all(len(point) == 2 and all(_number(v, 0, 1) for v in point)
                                                for point in ring) for ring in rings))
                if shape.kind == "Text":
                    fields = dict(shape.text)
                    fields["path"] = tuple(tuple(point) for point in fields.get("path", ()))
                    spec = TextSpec(**fields)
                    valid = valid and (not spec.styled or document["version"] >= 6)
                else:
                    spec = None
                valid = valid and (valid_text(spec) if spec else shape.text is None)
                shape = replace(shape, contours=rings, text=spec)
            else:
                valid = valid and not shape.contours and shape.text is None
            if shape.kind == "Sticker" and valid:
                sticker = Sticker(**shape.sticker)
                sticker.validate()
                shape = replace(shape, sticker=sticker)
            else:
                valid = valid and shape.sticker is None
        except (TypeError, ValueError, OverflowError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("Invalid template shape.")
        result.append(shape)
    return Project(*page, tuple(result), registration, mat, guides=guides)


def read_project(path, width, height):
    project = load_project(path)
    if (project.width, project.height) != (width, height):
        raise ValueError("Select the project's sheet size and orientation first.")
    return project.shapes


LOCKED_MESSAGE = "Unlock the selected object before editing it."


def has_locked(shapes, indices) -> bool:
    return any(shapes[i].locked for i in indices)


def clean_object_name(value: str) -> str:
    text = value.strip()
    if "\0" in text or len(text) > 100:
        raise ValueError("Enter a name up to 100 characters long.")
    return text


def object_rows(shapes, collapsed_groups=()):
    """Flat display rows for Objects: top-level shapes and collapsible groups."""
    collapsed = set(collapsed_groups)
    seen_groups = set()
    rows = []
    for index, shape in enumerate(shapes):
        group = shape.group
        if group is None:
            rows.append(("shape", index, 0))
            continue
        if group in seen_groups:
            continue
        seen_groups.add(group)
        members = tuple(i for i, item in enumerate(shapes) if item.group == group)
        rows.append(("group", group, members))
        if group not in collapsed:
            for member in members:
                rows.append(("shape", member, 1))
    return tuple(rows)


_KIND_LABELS = {
    "Rounded rectangle": "Rounded rect",
    "Rounded Rectangle": "Rounded rect",
    "Rectangle": "Rectangle",
    "Ellipse": "Ellipse",
    "Polygon": "Polygon",
    "Line": "Line",
    "Compound": "Compound path",
    "Path": "Path",
    "Sticker": "Sticker",
    "Text": "Text",
}


def _dimension_text(shape: Shape, *, unit: str = "mm", spaced: bool = False) -> str:
    scale = 1 / 25.4 if unit == "in" else 1.0
    digits = 2 if unit == "in" else 1
    w = abs(shape.width) * scale
    h = abs(shape.height) * scale
    if shape.kind == "Line":
        length = round((w * w + h * h) ** 0.5, digits)
        return f"{length:g} {unit}"
    if shape.kind == "Text" and shape.text and shape.text.value.strip():
        snippet = shape.text.value.strip().split("\n")[0][:12]
        return f"‘{snippet}’"
    joiner = " × " if spaced else "×"
    suffix = f" {unit}" if unit == "in" else ""
    return f"{round(w, digits):g}{joiner}{round(h, digits):g}{suffix}"


def object_name(shape: Shape) -> str:
    """Format an informative human-readable name for an object by user name or kind and size."""
    if shape.name.strip():
        return shape.name.strip()
    label = _KIND_LABELS.get(shape.kind, shape.kind)
    return f"{label} {_dimension_text(shape)}"


def object_display_name(shapes, index: int, *, unit: str = "mm") -> str:
    """Format a distinguishable row label for *shapes[index]* in the Objects hierarchy.

    Custom names are shown verbatim. Unnamed objects sharing a kind are numbered
    sequentially (for example, `Path 1 · 63 × 88` through `Path 8 · 63 × 88`) so
    identical shapes remain individually identifiable.
    """
    shape = shapes[index]
    if shape.name.strip():
        return shape.name.strip()
    label = _KIND_LABELS.get(shape.kind, shape.kind)
    same_kind = [i for i, item in enumerate(shapes)
                 if not item.name.strip() and _KIND_LABELS.get(item.kind, item.kind) == label]
    dim = _dimension_text(shape, unit=unit, spaced=True)
    if len(same_kind) > 1:
        ordinal = same_kind.index(index) + 1
        return f"{label} {ordinal} · {dim}"
    return f"{label} · {dim}"


def save_state_label(path, dirty: bool) -> str:
    """Short status badge for the document's save state."""
    if dirty:
        return "Unsaved changes" if path is not None else "Unsaved"
    return "Saved" if path is not None else ""


def document_summary(project: Project, cut_count: int, selected_count: int, *, unit: str = "mm") -> str:
    """Compact footer summary of page size, active cut paths, and selection."""
    paths = "1 path" if cut_count == 1 else f"{cut_count} paths"
    if unit == "in":
        w, h = round(project.width / 25.4, 2), round(project.height / 25.4, 2)
        size = f"{w:g} × {h:g} in"
    else:
        size = f"{project.width:g} × {project.height:g} mm"
    return f"{size} · {paths} · {selected_count} selected"


