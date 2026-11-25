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
    "ungroup": Tool("Ungroup", "", "Separate the grouped objects.",
                    (((3, 3), (12, 3), (12, 12), (3, 12), (3, 3)),
                     ((12, 12), (21, 12), (21, 21), (12, 21), (12, 12)))),
    "import": Tool("Import Artwork…", "", "Import an SVG, DXF, or Studio3 file onto the page.",
                   (((6, 3), (15, 3), (19, 7), (19, 21), (6, 21), (6, 3)),
                    ((15, 3), (15, 7), (19, 7)),
                    ((12, 10), (12, 18)), ((9, 13), (12, 10), (15, 13)))),
    "align": Tool("Align", "", "Align or space the selected objects.",
                  (((4, 3), (4, 21)), ((7, 6), (17, 6), (17, 10), (7, 10), (7, 6)),
                   ((7, 14), (20, 14), (20, 18), (7, 18), (7, 14)))),
    "path": Tool("Path", "", "", (((5, 17), (11, 6), (19, 12), (15, 20), (5, 17)),)),
}

def _tool(name):
    return TOOLS[TOOL_NAMES.index(name)]


# The Qt workspace's toolbox, laid out as Photoshop's: one Select tool whose
# selection always carries its transform box, so moving, scaling and rotating
# need no tools of their own, and a Type tool beside the drawing tools.
# GTK retains its separate Transform tools and shares the drawing tools it supports.
SELECT_TOOL = Tool("Select", "V", "Drag to move, corner to scale, top grip to rotate. Shift-click adds.",
                   _tool("Select").strokes)
TYPE_TOOL = Tool("Type", "T", "Click to place text, or click text to edit it.",
                 COMMAND_ICONS["text"].strokes)
POINT_EDIT_TOOL = Tool("Point Edit", "A", "Drag a point or blue handle. Click the outline to add a point.",
                       _tool("Point Edit").strokes)
LINE_TOOL = Tool(LINE, "L", "Drag to draw a line. Hold Shift for 45° steps.", _tool(LINE).strokes)
RECT_TOOL = Tool(RECTANGLE, "R", "Drag to draw a rectangle. Shift draws a square; Alt centers.",
                 _tool(RECTANGLE).strokes)
ROUNDED_TOOL = Tool(ROUNDED_RECTANGLE, "U", "Drag to draw a rounded rectangle. Shift draws a square; Alt centers.",
                    _tool(ROUNDED_RECTANGLE).strokes)
ELLIPSE_TOOL = Tool(ELLIPSE, "E", "Drag to draw an ellipse. Shift draws a circle; Alt centers.",
                    _tool(ELLIPSE).strokes)
REGULAR_POLYGON_TOOL = Tool("Regular Polygon", "G", "Drag to draw a polygon. Shift keeps it regular; Alt centers.",
                            (((12, 3), (20.6, 9.2), (17.3, 19.3), (6.7, 19.3), (3.4, 9.2), (12, 3)),))
POLYGON_TOOL = Tool("Polygon", "P", "Click points; double-click to finish open or click start to close.",
                    _tool("Polygon").strokes)
CURVE_TOOL = Tool("Curve", "C", "Click points for a smooth curve; double-click or click start to close.",
                  (((4, 18), (6, 11), (10, 6), (15, 6), (18, 10), (17, 15), (20, 19)),
                   ((3, 17), (5, 17), (5, 19), (3, 19), (3, 17)),
                   ((19, 18), (21, 18), (21, 20), (19, 20), (19, 18))))
ARC_TOOL = Tool("Arc", "", "Drag from one end of the arc to the other.",
                (((4, 20), (4, 15), (5.5, 10), (8.5, 6.5), (13, 4.5), (20, 4)),))
FREEHAND_TOOL = Tool("Freehand", "F", "Drag to draw. End where you started to close the shape.",
                     _tool("Freehand").strokes)
SMOOTH_FREEHAND_TOOL = Tool("Smooth Freehand", "", "Drag to draw a smooth line. End where you started to close.",
                            (((4, 17), (7, 9), (11, 12), (14, 6), (19, 9)), ((15, 15), (20, 20))))
KNIFE_TOOL = Tool("Knife", "K", "Drag across shapes to split them into separate pieces.",
                  (((4, 20), (17, 7), (20, 4), (21, 8), (8, 21), (4, 20)),))
ERASER_TOOL = Tool("Eraser", "X", "Drag over shapes to erase parts of them. Set size above.",
                   (((7, 20), (3, 16), (14, 5), (20, 11), (11, 20), (7, 20)), ((8, 11), (14, 17))))
EYEDROPPER_TOOL = Tool("Eyedropper", "I", "Click a shape to copy its action, line, and fill to the selection.",
                       (((14, 6), (18, 2.5), (21.5, 6), (18, 10), (14, 6)),
                        ((15, 7), (5.5, 16.5), (4, 20), (7.5, 18.5), (17, 9))))
MEASURE_TOOL = Tool("Measure", "M", "Drag across a known distance, then scale the selection to match.",
                    (((3, 16), (16, 3), (21, 8), (8, 21), (3, 16)),
                     ((7, 12), (9, 14)), ((10, 9), (13, 12)), ((13, 6), (15, 8))))
HAND_TOOL = Tool("Hand", "H", "Drag to scroll the page. Hold Space with any tool to do the same.",
                 (((8, 13), (8, 5), (10, 4), (11, 5), (11, 11), (11, 3), (13, 2), (14, 3), (14, 11), (14, 4),
                   (16, 3.5), (17, 5), (17, 12), (17, 7), (19, 6.5), (20, 8), (20, 15), (17, 20), (11, 21),
                   (7, 18), (4, 13), (5, 11), (8, 13)),))
ZOOM_TOOL = Tool("Zoom", "Z", "Click to zoom in; Alt-click to zoom out.",
                 (((15, 15), (21, 21)), ((10, 3), (14.9, 5.1), (17, 10), (14.9, 14.9), (10, 17), (5.1, 14.9),
                   (3, 10), (5.1, 5.1), (10, 3)), ((7, 10), (13, 10)), ((10, 7), (10, 13))))
WORKSPACE_TOOL_GROUPS = (
    ("Select", (SELECT_TOOL,)),
    ("Point Edit", (POINT_EDIT_TOOL,)),
    ("Shape tools", (RECT_TOOL, ROUNDED_TOOL, ELLIPSE_TOOL, REGULAR_POLYGON_TOOL, LINE_TOOL)),
    ("Path tools", (POLYGON_TOOL, CURVE_TOOL, ARC_TOOL, FREEHAND_TOOL, SMOOTH_FREEHAND_TOOL)),
    ("Type", (TYPE_TOOL,)),
    ("Cut tools", (KNIFE_TOOL, ERASER_TOOL)),
    ("Sample tools", (EYEDROPPER_TOOL, MEASURE_TOOL)),
    ("View tools", (HAND_TOOL, ZOOM_TOOL)),
)
GTK_TOOL_GROUPS = (
    *TOOL_GROUPS[:3],
    ("Shape tools", (*TOOL_GROUPS[3][1], REGULAR_POLYGON_TOOL)),
    ("Path tools", (_tool("Polygon"), CURVE_TOOL, ARC_TOOL, _tool("Freehand"), SMOOTH_FREEHAND_TOOL)),
)
GTK_TOOLS = tuple(tool for _, group in GTK_TOOL_GROUPS for tool in group)
GTK_TOOL_NAMES = tuple(tool.name for tool in GTK_TOOLS)
GTK_TOOL_KEYS = {ord(tool.shortcut): index for index, tool in enumerate(GTK_TOOLS) if tool.shortcut}

CLICK_PATH_TOOLS = ("Polygon", "Curve")
WORKSPACE_TOOLS = tuple(tool for _title, group in WORKSPACE_TOOL_GROUPS for tool in group)
WORKSPACE_TOOL_NAMES = tuple(tool.name for tool in WORKSPACE_TOOLS)
WORKSPACE_KEYS = {ord(tool.shortcut): index for index, tool in enumerate(WORKSPACE_TOOLS) if tool.shortcut}
DRAWING_TOOLS = (*KINDS, "Regular Polygon", "Polygon", "Curve", "Arc", "Freehand", "Smooth Freehand", "Type")
DRAG_SHAPE_TOOLS = (*KINDS, "Regular Polygon", "Arc")

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

    def sides(self):
        box = self.box
        return ((box.x + box.w / 2, box.y), (box.x2, box.y + box.h / 2),
                (box.x + box.w / 2, box.y2), (box.x, box.y + box.h / 2))

    def side_at(self, point):
        """(anchor, axis) for dragging a side handle: the opposite side stays
        put and the box stretches along one axis only."""
        box = self.box
        opposite = ((box.x, box.y2), (box.x, box.y), (box.x, box.y), (box.x2, box.y))
        axes = (1, 0, 1, 0)
        for (x, y), anchor, axis in zip(self.sides(), opposite, axes):
            if math.dist(point, (x, y)) <= 8 / self.scale and (box.h if axis else box.w) >= 4 / self.scale:
                return anchor, axis
        return None

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


# Panel icons for the dock's strip, one per Silhouette Studio panel.
def _box(x1, y1, x2, y2):
    return ((x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1))


PANEL_ICONS = {
    "page": Tool("Page Setup", "", "Page size, cutting mat and registration marks.",
                 (((6, 3), (15, 3), (19, 7), (19, 21), (6, 21), (6, 3)), ((15, 3), (15, 7), (19, 7)))),
    "transform": Tool("Transform", "", "Move, rotate, scale and shear the selection.",
                      (_box(5, 5, 19, 19), ((3, 12), (9, 12)), ((15, 12), (21, 12)), ((12, 3), (12, 9)),
                       ((12, 15), (12, 21)))),
    "align": Tool("Align", "", "Align and space selected objects.",
                  (((4, 3), (4, 21)), _box(7, 6, 17, 10), _box(7, 14, 20, 18))),
    "replicate": Tool("Replicate", "", "Copies in rows, columns, rotations or a full page.",
                      (_box(3, 3, 10, 10), _box(14, 3, 21, 10), _box(3, 14, 10, 21), _box(14, 14, 21, 21))),
    "modify": Tool("Modify", "", "Weld, subtract, intersect, divide and crop overlapping shapes.",
                   (_box(3, 3, 14, 14), _box(10, 10, 21, 21))),
    "offset": Tool("Offset", "", "Outline or inset a shape at a distance.",
                   (_box(8, 8, 16, 16), _box(4, 4, 20, 20))),
    "trace": Tool("Trace", "", "Make cut lines from a placed image.",
                  (((4, 9), (4, 4), (9, 4)), ((15, 4), (20, 4), (20, 9)), ((20, 15), (20, 20), (15, 20)),
                   ((9, 20), (4, 20), (4, 15)), ((8, 16), (11, 9), (14, 13), (16, 11), (17, 16), (8, 16)))),
    "text": Tool("Text Style", "", "Font, size, spacing and alignment of text.",
                 (((5, 5), (19, 5)), ((12, 5), (12, 19)), ((8, 19), (16, 19)))),
    "line": Tool("Line and Fill", "", "Cut action, line color and style, and fill.",
                 (((4, 20), (20, 4)), _box(12, 13, 20, 21))),
    "grid": Tool("Grid", "", "Grid, snapping, rulers and crosshairs.",
                 (_box(3, 3, 21, 21), ((9, 3), (9, 21)), ((15, 3), (15, 21)), ((3, 9), (21, 9)), ((3, 15), (21, 15)))),
    "nesting": Tool("Nesting", "", "Pack shapes into the cut area or a border to save material.",
                    (_box(3, 3, 21, 21), _box(5, 5, 12, 10), _box(13, 5, 19, 13), _box(5, 12, 11, 19),
                     _box(13, 15, 19, 19))),
    "objects": Tool("Objects", "", "Every object in the design, front to back.",
                    (((4, 8), (12, 4), (20, 8), (12, 12), (4, 8)), ((4, 12), (12, 16), (20, 12)),
                     ((4, 16), (12, 20), (20, 16)))),
}


def contextual_hint(tool, *, shape_count, selected_count, is_sticker=False, is_text=False,
                    needs_trace=False, node_selected=False):
    """Short next-step cue for the options bar based on document and tool state."""
    if tool.name == "Select":
        if shape_count == 0:
            return "Blank page — draw with a tool, click Add Shape, or Import Artwork."
        if selected_count == 1 and is_sticker:
            return ("Image selected — click Trace and Bleed… to make its cut outline."
                    if needs_trace else "Image selected — drag to place, or Trace and Bleed… to adjust.")
        if selected_count == 1 and is_text:
            return "Text selected — click Edit Text… or double-click to edit."
        if selected_count >= 2:
            return f"{selected_count} selected — use Group or Align above, or drag together."
    elif tool.name == "Point Edit":
        if selected_count != 1:
            return "Click an outline to edit its points."
        if not node_selected:
            return "Drag a point or click the outline to add one."
    return tool.hint
