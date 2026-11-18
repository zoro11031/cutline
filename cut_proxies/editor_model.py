"""Editable cutter shapes and versioned template projects in millimetres."""

from dataclasses import asdict, dataclass, replace
import json
import math
import re
import tempfile

from . import actions, geometry, graphtec, importer, regmarks, template, toolpath


KINDS = ("Rectangle", "Rounded rectangle", "Ellipse", "Line")
ACTIONS = ("Cut", "Score", "None")
LINE_STYLES = {"solid": "Solid", "dashed": "Dashed", "dotted": "Dotted"}
ALL_ACTIONS = "All actions"
REVIEW_ACTIONS = (*ACTIONS[:2], ALL_ACTIONS)
ALIGNMENTS = ("Align Left", "Center Horizontally", "Align Right",
              "Align Top", "Center Vertically", "Align Bottom",
              "Space Horizontally", "Space Vertically")
SHAPE_LIMIT = 1000
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")


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

    def path(self):
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
        if self.rotation or self.flip_x or self.flip_y:
            cx, cy = self.x + self.width/2, self.y + self.height/2
            r = math.radians(self.rotation)
            c, s = math.cos(r), math.sin(r)
            fx, fy = (-1 if self.flip_x else 1), (-1 if self.flip_y else 1)
            points = tuple((cx+c*fx*(x-cx)-s*fy*(y-cy), cy+s*fx*(x-cx)+c*fy*(y-cy))
                           for x, y in points)
        closed = self.closed if self.kind == "Path" else self.kind != "Line"
        return template.CutPath(points, closed=closed,
                                label=f"{self.kind}|{self.stroke}" if self.stroke else self.kind,
                                fill=self.fill)


def bounds(shapes):
    """Bounds of the actual transformed paths."""
    xs, ys = zip(*(point for shape in shapes for point in shape.path().points))
    return geometry.Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def hit(shape, point, tolerance):
    path = shape.path()
    edges = toolpath._edges(path.points, path.closed)
    if path.closed and toolpath._inside(point, edges):
        return True
    for a, b in edges:
        dx, dy = b[0] - a[0], b[1] - a[1]
        t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / (dx*dx + dy*dy) if a != b else 0
        t = max(0, min(1, t))
        if math.dist(point, (a[0] + t*dx, a[1] + t*dy)) <= tolerance:
            return True
    return False


def move(shapes, indices, dx, dy):
    selected = [shapes[i] for i in indices]
    if not selected:
        return shapes
    dx = max(-SHAPE_LIMIT - min(s.x for s in selected), min(dx, SHAPE_LIMIT - max(s.x for s in selected)))
    dy = max(-SHAPE_LIMIT - min(s.y for s in selected), min(dy, SHAPE_LIMIT - max(s.y for s in selected)))
    return tuple(replace(shape, x=shape.x + dx, y=shape.y + dy) if i in indices else shape
                 for i, shape in enumerate(shapes))


def snap_move(shapes, indices, dx, dy, width, height, tolerance):
    selection = bounds(shapes[i] for i in indices)
    others = [bounds((shape,)) for i, shape in enumerate(shapes) if shape.visible and i not in indices]
    targets = ([0, width / 2, width], [0, height / 2, height])
    for box in others:
        targets[0].extend((box.x, box.x + box.w / 2, box.x2))
        targets[1].extend((box.y, box.y + box.h / 2, box.y2))
    moves = []
    for axis, size, delta, guides in zip(("x", "y"), ("w", "h"), (dx, dy), targets):
        start, length = getattr(selection, axis) + delta, getattr(selection, size)
        shift = min((guide - (start + length * anchor) for guide in guides for anchor in (0, 0.5, 1)), key=abs)
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
    if not indices:
        return shapes
    area = bounds(shapes[i] for i in indices)
    cx, cy = area.x + area.w / 2, area.y + area.h / 2
    result = list(shapes)
    for index in indices:
        shape = shapes[index]
        x, y = shape.x + shape.width / 2, shape.y + shape.height / 2
        if operation == "left":
            result[index] = _place_center(shape, cx + y - cy, cy - x + cx,
                                          rotation=(shape.rotation - 90) % 360)
        elif operation == "right":
            result[index] = _place_center(shape, cx - y + cy, cy + x - cx,
                                          rotation=(shape.rotation + 90) % 360)
        elif operation == "horizontal":
            result[index] = _place_center(shape, 2 * cx - x, y,
                                          rotation=-shape.rotation, flip_x=not shape.flip_x)
        elif operation == "vertical":
            result[index] = _place_center(shape, x, 2 * cy - y,
                                          rotation=-shape.rotation, flip_y=not shape.flip_y)
    return tuple(result)


def matching_color(shapes, indices, field):
    colors = {getattr(shapes[index], field) for index in indices}
    return tuple(i for i, shape in enumerate(shapes) if shape.visible and getattr(shape, field) in colors)


def convert_path(shape, simplify=False):
    path = shape.path()
    points = path.points
    if simplify and len(points) > (3 if path.closed else 2):
        if path.closed:
            split = max(range(1, len(points)), key=lambda i: math.dist(points[0], points[i]))
            first = _simplify_line(points[:split + 1], 0.1)
            last = _simplify_line((*points[split:], points[0]), 0.1)
            reduced = (*first[:-1], *last[:-1])
            if len(reduced) >= 3:
                points = reduced
        else:
            points = _simplify_line(points, 0.1)
    converted = shape_from_path(replace(path, points=points))
    return replace(converted, stroke=shape.stroke, fill=shape.fill, visible=shape.visible,
                   action=shape.action, group=shape.group, holder=shape.holder,
                   line_style=shape.line_style, line_width_mm=shape.line_width_mm)


def offset_shape(shape, distance):
    path = shape.path()
    points = path.points
    if not path.closed or len(points) < 3 or not math.isfinite(distance) or distance == 0:
        raise ValueError("Choose a closed shape and a nonzero offset.")
    edges = [(a, b) for a, b in toolpath._edges(points, True) if math.dist(a, b) > 1e-8]
    area = sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)
    if len(edges) < 3 or abs(area) < 1e-8:
        raise ValueError("The shape has no area to offset.")
    side = math.copysign(1, area)
    directions = [(b[0] - a[0], b[1] - a[1]) for a, b in edges]
    normal = [(side * dy / math.hypot(dx, dy), -side * dx / math.hypot(dx, dy))
              for dx, dy in directions]
    result = []
    winding = 0.0
    for i, ((x, y), _) in enumerate(edges):
        dx, dy = directions[i]
        previous = directions[i - 1]
        turn = previous[0] * dy - previous[1] * dx
        if side * turn < -1e-8:
            raise ValueError("Offset works with closed shapes without inward corners.")
        winding += math.atan2(side * turn, previous[0] * dx + previous[1] * dy)
        nx, ny = normal[i]
        px, py = normal[i - 1]
        dot = 1 + nx * px + ny * py
        if dot < 1e-8:
            raise ValueError("A corner is too sharp to offset.")
        result.append((x + distance * (nx + px) / dot, y + distance * (ny + py) / dot))
    if not math.isclose(winding, math.tau, abs_tol=1e-6):
        raise ValueError("Offset works with simple closed shapes.")
    for ((a, b), (dx, dy)) in zip(toolpath._edges(tuple(result), True), directions):
        if (b[0] - a[0]) * dx + (b[1] - a[1]) * dy <= 1e-8:
            raise ValueError("This inset is too large for the shape.")
    box = shape_from_path(replace(path, points=tuple(result)))
    return replace(box, stroke=shape.stroke, fill=shape.fill, visible=shape.visible, action=shape.action,
                   holder=shape.holder, line_style=shape.line_style, line_width_mm=shape.line_width_mm)


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
    paths = tuple(replace(shape.path(), layer=((shape.action, actions.HOLDER_SETTINGS[shape.holder])
                                              if shape.holder else (shape.action,))) for shape in shapes
                  if shape.visible and shape.action != "None"
                  and action in (shape.action, ALL_ACTIONS))
    return importer.Imported(paths, width, height,
                             "Template editor", "editor coordinates in millimetres",
                             page_size_mm=(width, height), use_document_page=True)


def holder_defaults(drawing):
    labels = set(actions.HOLDER_SETTINGS.values())
    groups = actions.group_paths(drawing.paths, "layer")
    if any(key.rsplit(" / ", 1)[-1] in labels for key in groups):
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

    def artwork(self, action="Cut"):
        return artwork(self.shapes, self.width, self.height, action)


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


def save_project(path, project):
    objects = []
    for shape in project.shapes:
        fields = asdict(shape)
        for name, default in (("holder", None), ("line_style", "solid"), ("line_width_mm", 0.0)):
            if fields[name] == default:
                del fields[name]
        objects.append(fields)
    document = {"version": 2, "page_mm": [project.width, project.height],
                "registration": asdict(project.registration) if project.registration else None,
                "mat": project.mat, "objects": objects}
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


def move_vertex(shape, index, position):
    path = shape.path()
    points = path.points[:index] + (position,) + path.points[index+1:]
    edited = shape_from_path(replace(path, points=points))
    return replace(edited, action=shape.action, visible=shape.visible, group=shape.group, holder=shape.holder,
                   line_style=shape.line_style, line_width_mm=shape.line_width_mm)


def load_project(path, legacy_registration=None):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") not in (1, 2):
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
    shapes = document.get("shapes" if legacy else "objects")
    if not isinstance(shapes, list):
        raise ValueError("Missing template shapes.")
    result = []
    for item in shapes:
        try:
            fields = {"kind", "x", "y", "width", "height", "radius"} if legacy else Shape.__dataclass_fields__.keys()
            required = fields if legacy else fields - {"stroke", "fill", "group", "holder", "line_style", "line_width_mm"}
            if not isinstance(item, dict) or not required <= item.keys() <= fields:
                raise TypeError("incomplete shape")
            shape = Shape(**item)
            numbers = (shape.x, shape.y, shape.width, shape.height, shape.radius)
            valid = (shape.kind in (KINDS if legacy else (*KINDS, "Path")) and
                     all(_number(v) for v in numbers) and shape.radius >= 0 and
                     _number(shape.rotation, -360, 360) and shape.action in ACTIONS and
                     (shape.holder is None or (type(shape.holder) is int and shape.holder in (1, 2))) and
                     shape.line_style in LINE_STYLES and _number(shape.line_width_mm, 0, 5) and
                     (shape.group is None or (type(shape.group) is int and shape.group > 0)) and
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
        except (TypeError, ValueError, OverflowError, AttributeError):
            valid = False
        if not valid:
            raise ValueError("Invalid template shape.")
        result.append(shape)
    return Project(*page, tuple(result), registration, mat)


def read_project(path, width, height):
    project = load_project(path)
    if (project.width, project.height) != (width, height):
        raise ValueError("Select the project's sheet size and orientation first.")
    return project.shapes

