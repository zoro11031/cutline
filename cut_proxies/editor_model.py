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
PATH_OPERATIONS = ("Make Compound Path", "Release Compound Path", "Weld", "Subtract", "Intersect")


@dataclass(frozen=True)
class TextSpec:
    value: str
    family: str
    size_mm: float


def valid_text(spec):
    return (isinstance(spec.value, str) and 1 <= len(spec.value) <= 2048 and bool(spec.value.strip())
            and "\0" not in spec.value and isinstance(spec.family, str) and 1 <= len(spec.family) <= 200
            and "\0" not in spec.family and _number(spec.size_mm, 1, 100))


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
            points = tuple((self.x + x * self.width, self.y + y * self.height)
                           for x, y in self.points)
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


def scale_selection(shapes, indices, anchor, factor):
    """Scale selected objects proportionally about a fixed paper coordinate."""
    if not indices or factor == 1:
        return shapes
    if not math.isfinite(factor) or factor <= 0:
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
        if max(abs(width), abs(height)) < 0.1 or (
                shape.kind not in ("Line", "Path") and min(width, height) < 0.1):
            raise ValueError("The shape is too small to scale further.")
        result[index] = replace(shape, x=x, y=y, width=width, height=height, radius=radius)
    return tuple(result)


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


def offset_shape(shape, distance):
    if not math.isfinite(distance) or not distance:
        raise ValueError("Enter a nonzero offset distance.")
    result = vector_regions.modify("Offset", (_closed_contours(shape),), distance=distance)
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
    already in Send, changes nothing, and neither does one with nothing to
    cut, so a look at Design never replaces Send's artwork.

    Returns (verdict, action): "apply", "keep", "empty", "stickers" (those
    go through Export Print & Cut) or "invalid" (refused, with the reason
    as the second item)."""
    if project == applied or (applied is None and project == origin):
        return "keep", None
    if any(shape.visible and shape.sticker for shape in project.shapes):
        return "stickers", None
    action = send_action(project.shapes)
    if action is None:
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
    return version


def project_document(project):
    objects = []
    for shape in project.shapes:
        fields = asdict(shape)
        for name, default in (("holder", None), ("line_style", "solid"), ("line_width_mm", 0.0),
                              ("text", None), ("contours", ()), ("sticker", None), ("layer", "")):
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
                   layer=original.layer)


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


def load_project(path, legacy_registration=None):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") not in (1, 2, 3, 4, 5):
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
                                                        "line_width_mm", "contours", "text", "sticker", "layer"}
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
                     all(type(v) is bool for v in (shape.closed, shape.visible, shape.flip_x, shape.flip_y)))
            shape = replace(shape, stroke=stroke_color(shape.stroke), fill=stroke_color(shape.fill))
            if shape.kind == "Path":
                points = tuple(tuple(p) for p in shape.points)
                valid = (valid and len(points) >= 2 and min(shape.width, shape.height) >= 0 and
                         all(len(p) == 2 and all(_number(v, 0, 1) for v in p) for p in points))
                shape = replace(shape, points=points)
            else:
                valid = (valid and not shape.points and
                         (shape.kind == "Line" or min(shape.width, shape.height) >= 0.1))
                shape = replace(shape, points=())
            if shape.kind in MULTI_CONTOUR_KINDS:
                rings = tuple(tuple(tuple(point) for point in ring) for ring in shape.contours)
                valid = (valid and shape.closed and (bool(rings) or shape.kind == "Sticker") and all(
                    len(set(ring)) >= 3 and all(len(point) == 2 and all(_number(v, 0, 1) for v in point)
                                                for point in ring) for ring in rings))
                spec = TextSpec(**shape.text) if shape.kind == "Text" else None
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


def object_name(shape: Shape) -> str:
    """Format an informative human-readable name for an object by kind and size."""
    kind_labels = {
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
    label = kind_labels.get(shape.kind, shape.kind)
    w = abs(shape.width)
    h = abs(shape.height)
    if shape.kind == "Line":
        length = round((w * w + h * h) ** 0.5, 1)
        dim = f"{length:g} mm"
    elif shape.kind == "Text" and shape.text and getattr(shape.text, "text", ""):
        snippet = shape.text.text.strip().split("\n")[0][:12]
        dim = f"‘{snippet}’"
    else:
        dim = f"{round(w, 1):g}×{round(h, 1):g}"
    return f"{label} {dim}"


