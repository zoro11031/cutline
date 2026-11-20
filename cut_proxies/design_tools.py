"""Names, shortcuts and 24-unit icon outlines shared by both editors."""

from dataclasses import dataclass
import math

from .editor_model import ADD_SHAPE_TIP, ALIGNMENTS, KINDS, bounds, rotate_point
from .geometry import Rect


@dataclass(frozen=True)
class Tool:
    name: str
    shortcut: str
    hint: str
    strokes: tuple[tuple[tuple[float, float], ...], ...]

    @property
    def tooltip(self):
        title = f"{self.name} ({self.shortcut})" if self.shortcut else self.name
        return f"{title}\n{self.hint}"


RECTANGLE, ROUNDED_RECTANGLE, ELLIPSE, LINE = KINDS
TURN_LEFT = (((8, 4), (4, 8), (9, 10)),
             ((4, 8), (8, 5), (13, 4), (17, 6), (20, 10), (20, 15), (17, 19), (12, 20), (8, 18)))
TURN_RIGHT = tuple(tuple((24 - x, y) for x, y in stroke) for stroke in TURN_LEFT)
TRANSFORM_MODES = ("Transform", "Rotate", "Scale")
ROTATE_MODES = ("Transform", "Rotate")
SCALE_MODES = ("Transform", "Scale")

# The first tool in each slot is its default. A slot remembers its last choice.
TOOL_GROUPS = (
    ("Select", (
        Tool("Select", "V", "Select or move objects. Shift-click adds to the selection.",
             (((5, 3), (5, 19), (9, 15), (12, 21), (15, 19), (12, 13), (18, 13), (5, 3)),)),
    )),
    ("Point Edit", (
        Tool("Point Edit", "A", "Select a path and drag its points.",
             (((5, 17), (12, 6), (19, 15)),
              ((3, 16), (7, 16), (7, 20), (3, 20), (3, 16)),
              ((10, 4), (14, 4), (14, 8), (10, 8), (10, 4)),
              ((17, 14), (21, 14), (21, 18), (17, 18), (17, 14)))),
    )),
    ("Transform tools", (
        Tool("Transform", "T", "Drag inside to move, a corner to scale, or the round handle to rotate.",
             (((4, 4), (20, 4), (20, 20), (4, 20), (4, 4)),
              ((14, 10), (18, 6)), ((15, 6), (18, 6), (18, 9)),
              ((10, 14), (6, 18)), ((6, 15), (6, 18), (9, 18)))),
        Tool("Rotate", "", "Drag an object or the round handle to rotate. Hold Shift for 15° steps.", TURN_RIGHT),
        Tool("Scale", "S", "Drag a corner to scale proportionally from the opposite corner.",
             (((4, 7), (4, 20), (17, 20)), ((20, 17), (20, 4), (7, 4)),
              ((12, 12), (20, 4)), ((14, 4), (20, 4), (20, 10)))),
    )),
    ("Shape tools", (
        Tool(RECTANGLE, "R", "Drag to draw a rectangle.",
             (((4, 6), (20, 6), (20, 18), (4, 18), (4, 6)),)),
        Tool(ROUNDED_RECTANGLE, "U", "Drag to draw a rectangle with rounded corners.",
             (((8, 6), (16, 6), (18.8, 7.2), (20, 10), (20, 14), (18.8, 16.8),
               (16, 18), (8, 18), (5.2, 16.8), (4, 14), (4, 10), (5.2, 7.2), (8, 6)),)),
        Tool(ELLIPSE, "E", "Drag to draw an ellipse.",
             (((20, 12), (19.4, 14.7), (17.7, 16.9), (15.1, 18.5), (12, 19),
               (8.9, 18.5), (6.3, 16.9), (4.6, 14.7), (4, 12), (4.6, 9.3),
               (6.3, 7.1), (8.9, 5.5), (12, 5), (15.1, 5.5), (17.7, 7.1), (19.4, 9.3), (20, 12)),)),
        Tool(LINE, "L", "Drag between the endpoints of a straight line.",
             (((5, 19), (19, 5)),)),
    )),
    ("Path tools", (
        Tool("Polygon", "P", "Click to place corners. Press Enter to close the shape.",
             (((12, 4), (20, 10), (17, 19), (7, 19), (4, 10), (12, 4)),)),
        Tool("Freehand", "F", "Drag to draw a freehand path.",
             (((5, 19), (7, 13), (16, 4), (20, 8), (11, 17), (5, 19)),
              ((7, 13), (11, 17)), ((14, 6), (18, 10)))),
    )),
)
TOOLS = tuple(tool for _title, group in TOOL_GROUPS for tool in group)
TOOL_NAMES = tuple(tool.name for tool in TOOLS)
TOOL_KEYS = {ord(tool.shortcut): index for index, tool in enumerate(TOOLS) if tool.shortcut}

COMMAND_ICONS = {
    "save": Tool("Save", "", "Save the design.",
                 (((4, 4), (17, 4), (20, 7), (20, 20), (4, 20), (4, 4)),
                  ((8, 4), (8, 10), (16, 10), (16, 4)),
                  ((8, 20), (8, 14), (16, 14), (16, 20)))),
    "undo": Tool("Undo", "", "Undo the last change.",
                 (((10, 5), (5, 10), (10, 15)),
                  ((5, 10), (14, 10), (17, 11), (19, 14), (19, 18)))),
    "redo": Tool("Redo", "", "Redo the last change.",
                 (((14, 5), (19, 10), (14, 15)),
                  ((19, 10), (10, 10), (7, 11), (5, 14), (5, 18)))),
    "shape": Tool("Add Shape", "", ADD_SHAPE_TIP,
                  (((13, 17), (4, 17), (4, 5), (17, 5), (17, 12)),
                   ((17, 14), (17, 21)), ((13.5, 17.5), (20.5, 17.5)))),
    "text": Tool("Add Text…", "", "Add text to the design.",
                 (((5, 5), (19, 5)), ((12, 5), (12, 19)), ((8, 19), (16, 19)),
                  ((5, 5), (5, 8)), ((19, 5), (19, 8)))),
    "image": Tool("Place Image…", "", "Insert a photo or other image.",
                  (((3, 5), (21, 5), (21, 19), (3, 19), (3, 5)),
                   ((3, 16), (8, 11), (13, 16), (16.5, 13), (21, 17)),
                   ((16.5, 9), (17.3, 9)))),
    "trace": Tool("Trace and Bleed…", "", "Trace the selected image and create bleed.",
                  (((4, 9), (4, 4), (9, 4)), ((15, 4), (20, 4), (20, 9)),
                   ((20, 15), (20, 20), (15, 20)), ((9, 20), (4, 20), (4, 15)),
                   ((7.5, 15), (11, 9), (14, 13), (16.5, 11), (17, 16), (7.5, 15)))),
    "fit": Tool("Fit to view", "", "Fit the page in the canvas.",
                (((4, 9), (4, 4), (9, 4)), ((15, 4), (20, 4), (20, 9)),
                 ((20, 15), (20, 20), (15, 20)), ((9, 20), (4, 20), (4, 15)),
                 ((9, 8), (15, 8), (15, 16), (9, 16), (9, 8)))),
    "color": Tool("Pick Color…", "", "Choose a color.",
                  (((12, 7), (17, 2.5), (21.5, 7), (17, 12), (12, 7)),
                   ((13, 8), (5.5, 15.5), (4, 20), (8.5, 18.5), (16, 11)))),
    "duplicate": Tool("Duplicate", "", "Duplicate the selected objects.",
                      (((7, 7), (7, 4), (20, 4), (20, 17), (17, 17)),
                       ((4, 7), (17, 7), (17, 20), (4, 20), (4, 7)))),
    "delete": Tool("Delete", "", "Delete the selected objects.",
                   (((4, 7), (20, 7)), ((9, 7), (9, 4), (15, 4), (15, 7)),
                    ((6, 7), (7, 20), (17, 20), (18, 7)), ((10, 10), (10, 17)), ((14, 10), (14, 17)))),
    "more": Tool("Object options", "", "Arrange or group selected objects.",
                 (((6, 12), (6.5, 12)), ((11.75, 12), (12.25, 12)), ((17.5, 12), (18, 12)))),
    "snap": Tool("Snap to Grid", "", "Snap drawing and object moves to the grid.",
                 (((6, 4), (6, 14), (8, 18), (12, 20), (16, 18), (18, 14), (18, 4)),
                  ((10, 4), (10, 14), (12, 16), (14, 14), (14, 4)),
                  ((6, 4), (10, 4)), ((14, 4), (18, 4)), ((6, 8), (10, 8)), ((14, 8), (18, 8)))),
    "group": Tool("Group", "", "Group the selected objects.",
                  (((3, 3), (21, 3), (21, 21), (3, 21), (3, 3)),
                   ((6, 6), (12, 6), (12, 13), (6, 13), (6, 6)),
                   ((15, 11), (18, 11), (18, 18), (11, 18), (11, 16)))),
    "path": Tool("Path", "", "", (((5, 17), (11, 6), (19, 12), (15, 20), (5, 17)),)),
}

OBJECT_ICONS = {tool.name: tool for tool in TOOLS if tool.name in (*KINDS, "Polygon")}
OBJECT_ICONS.update(Path=COMMAND_ICONS["path"], Compound=COMMAND_ICONS["group"],
                    Text=COMMAND_ICONS["text"], Sticker=COMMAND_ICONS["image"])
FLIP = (((12, 3), (12, 21)), ((8, 6), (3, 12), (8, 18), (8, 6)),
        ((16, 6), (21, 12), (16, 18), (16, 6)))
TRANSFORM_ICONS = {
    "left": Tool("Rotate 90° counterclockwise", "", "Rotate the selected objects around their shared center.", TURN_LEFT),
    "right": Tool("Rotate 90° clockwise", "", "Rotate the selected objects around their shared center.", TURN_RIGHT),
    "horizontal": Tool("Flip horizontally", "", "Mirror the selection left to right.", FLIP),
    "vertical": Tool("Flip vertically", "", "Mirror the selection top to bottom.",
                     tuple(tuple((y, x) for x, y in stroke) for stroke in FLIP)),
}

_ALIGN_HORIZONTAL = (
    (((4, 4), (4, 20)), ((8, 7), (17, 7)), ((8, 12), (20, 12)), ((8, 17), (15, 17))),
    (((12, 3), (12, 21)), ((7, 7), (17, 7)), ((4, 12), (20, 12)), ((8, 17), (16, 17))),
    (((20, 4), (20, 20)), ((7, 7), (16, 7)), ((4, 12), (16, 12)), ((9, 17), (16, 17))),
    (((4, 4), (4, 20)), ((20, 4), (20, 20)), ((9.5, 7), (9.5, 17)), ((14.5, 7), (14.5, 17))),
)
ALIGNMENT_ROWS = (
    tuple(Tool(ALIGNMENTS[i], "", "Align or space the selected objects.", strokes)
          for i, strokes in zip((0, 1, 2, 6), _ALIGN_HORIZONTAL)),
    tuple(Tool(ALIGNMENTS[i], "", "Align or space the selected objects.",
               tuple(tuple((y, x) for x, y in stroke) for stroke in strokes))
          for i, strokes in zip((3, 4, 5, 7), _ALIGN_HORIZONTAL)),
)


@dataclass(frozen=True)
class RotationHandle:
    box: Rect
    scale: float

    @property
    def center(self):
        return self.box.x + self.box.w / 2, self.box.y + self.box.h / 2

    def positions(self, angle=0):
        x, _ = self.center
        top = self.box.y
        return (rotate_point((x, top), self.center, angle),
                rotate_point((x, top - 23 / self.scale), self.center, angle))

    def hit(self, point):
        return math.dist(point, self.positions()[1]) <= 11 / self.scale

    def corners(self):
        box = self.box
        return ((box.x, box.y), (box.x2, box.y), (box.x2, box.y2), (box.x, box.y2))

    def opposite_corner(self, point):
        corners = self.corners()
        index = min(range(4), key=lambda i: math.dist(point, corners[i]))
        corner, opposite = corners[index], corners[(index + 2) % 4]
        if math.dist(point, corner) <= 8 / self.scale and math.dist(corner, opposite) >= 4 / self.scale:
            return opposite
        return None

    def angle(self, start, end, *, snap=False):
        cx, cy = self.center
        if min(math.dist(start, self.center), math.dist(end, self.center)) <= 3 / self.scale:
            return None
        first = math.atan2(start[1] - cy, start[0] - cx)
        last = math.atan2(end[1] - cy, end[0] - cx)
        angle = (math.degrees(last - first) + 180) % 360 - 180
        return 15 * round(angle / 15) if snap else round(angle, 3)


def scale_from_corner(start, end, anchor):
    """Project the pointer onto the corner diagonal to keep proportions."""
    x, y = start[0] - anchor[0], start[1] - anchor[1]
    dx, dy = end[0] - anchor[0], end[1] - anchor[1]
    return round((x * dx + y * dy) / (x * x + y * y), 5)


def rotation_handle(shapes, indices, scale):
    if any(shapes[i].visible for i in indices):
        return RotationHandle(bounds(shapes[i] for i in indices), scale)
    return None
