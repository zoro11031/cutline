"""Editable cutter shapes and versioned template projects in millimetres."""

from dataclasses import asdict, dataclass, replace
import json
import math
import tempfile

from . import geometry, importer, regmarks, template


KINDS = ("Rectangle", "Rounded rectangle", "Ellipse", "Line")
ACTIONS = ("Cut", "Score", "None")
ALL_ACTIONS = "All actions"
REVIEW_ACTIONS = (*ACTIONS[:2], ALL_ACTIONS)


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
        return template.CutPath(points, closed=closed, label=self.kind)


def artwork(shapes, width, height, action="Cut"):
    paths = tuple(replace(shape.path(), layer=(shape.action,)) for shape in shapes
                  if shape.visible and shape.action != "None"
                  and action in (shape.action, ALL_ACTIONS))
    return importer.Imported(paths, width, height,
                             "Template editor", "editor coordinates in millimetres",
                             page_size_mm=(width, height))


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
    document = {"version": 2, "page_mm": [project.width, project.height],
                "registration": asdict(project.registration) if project.registration else None,
                "objects": [asdict(s) for s in project.shapes]}
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


def _number(value, low=-1000, high=1000):
    return type(value) in (int, float) and low <= value <= high


def import_shapes(path):
    return editable_import(importer.load(path))


def editable_import(drawing):
    omissions = [w for w in drawing.warnings if w.startswith((
        "ignored ", "the file uses CSS", "Excluded Inkscape layers"))]
    if omissions:
        raise ValueError("\n".join(omissions))
    return shapes_from_paths(drawing.paths), "\n".join([drawing.scale_note, *drawing.warnings])


def shapes_from_paths(paths):
    shapes = []
    for contour in paths:
        xs, ys = zip(*contour.points)
        x, y = min(xs), min(ys)
        w, h = max(xs)-x, max(ys)-y
        points = tuple(((u-x)/(w or 1), (v-y)/(h or 1)) for u, v in contour.points)
        shapes.append(Shape("Path", x, y, w, h, points=points, closed=contour.closed))
    if any(not all(_number(v) for v in (s.x, s.y, s.width, s.height)) for s in shapes):
        raise ValueError("The vector positions are outside the design workspace's editable range.")
    return tuple(shapes)


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
    shapes = document.get("shapes" if legacy else "objects")
    if not isinstance(shapes, list):
        raise ValueError("Missing template shapes.")
    result = []
    for item in shapes:
        try:
            fields = {"kind", "x", "y", "width", "height", "radius"} if legacy else Shape.__dataclass_fields__.keys()
            if not isinstance(item, dict) or item.keys() != fields:
                raise TypeError("incomplete shape")
            shape = Shape(**item)
            numbers = (shape.x, shape.y, shape.width, shape.height, shape.radius)
            valid = (shape.kind in (KINDS if legacy else (*KINDS, "Path")) and
                     all(_number(v) for v in numbers) and shape.radius >= 0 and
                     _number(shape.rotation, -360, 360) and shape.action in ACTIONS and
                     all(type(v) is bool for v in (shape.closed, shape.visible, shape.flip_x, shape.flip_y)))
            if shape.kind == "Path":
                points = tuple(tuple(p) for p in shape.points)
                valid = (valid and len(points) >= 2 and min(shape.width, shape.height) >= 0 and
                         all(len(p) == 2 and all(_number(v, 0, 1) for v in p) for p in points))
                shape = replace(shape, points=points)
            else:
                valid = (valid and not shape.points and
                         (shape.kind == "Line" or min(shape.width, shape.height) >= 0.1))
                shape = replace(shape, points=())
        except (TypeError, OverflowError):
            valid = False
        if not valid:
            raise ValueError("Invalid template shape.")
        result.append(shape)
    return Project(*page, tuple(result), registration)


def read_project(path, width, height):
    project = load_project(path)
    if (project.width, project.height) != (width, height):
        raise ValueError("Select the project's sheet size and orientation first.")
    return project.shapes

