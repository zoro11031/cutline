"""Editable cutter shapes and versioned template projects in millimetres."""

from dataclasses import asdict, dataclass
import json
import math

from . import geometry, importer, template


KINDS = ("Rectangle", "Rounded rectangle", "Ellipse", "Line")


@dataclass(frozen=True)
class Shape:
    kind: str
    x: float = 45
    y: float = 45
    width: float = 40
    height: float = 30
    radius: float = 3

    def path(self):
        rect = geometry.Rect(self.x, self.y, self.width, self.height)
        if self.kind == "Line":
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
        return template.CutPath(points, closed=self.kind != "Line", label=self.kind)


def artwork(shapes, width, height):
    return importer.Imported(tuple(shape.path() for shape in shapes), width, height,
                             "Template editor", "editor coordinates in millimetres",
                             page_size_mm=(width, height))


def write_project(path, shapes, width, height):
    document = {"version": 1, "page_mm": [width, height],
                "shapes": [asdict(s) for s in shapes]}
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def read_project(path, width, height):
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("version") != 1:
        raise ValueError("Unsupported template project version.")
    if document.get("page_mm") != [width, height]:
        raise ValueError("Select the project's sheet size and orientation first.")
    shapes = document.get("shapes")
    if not isinstance(shapes, list):
        raise ValueError("Missing template shapes.")
    result = []
    for item in shapes:
        try:
            if not isinstance(item, dict) or item.keys() != Shape.__dataclass_fields__.keys():
                raise TypeError("incomplete shape")
            shape = Shape(**item)
            numbers = (shape.x, shape.y, shape.width, shape.height, shape.radius)
            valid = (shape.kind in KINDS and all(
                type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1000
                for v in numbers) and shape.radius >= 0
                and (shape.kind == "Line" or min(shape.width, shape.height) >= 0.1))
        except (TypeError, OverflowError):
            valid = False
        if not valid:
            raise ValueError("Invalid template shape.")
        result.append(shape)
    return tuple(result)

