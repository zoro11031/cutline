"""Names, shortcuts and 24-unit icon outlines shared by both editors."""

from dataclasses import dataclass

from .editor_model import ADD_SHAPE_TIP, KINDS


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

# Editing and drawing are the two groups in the canvas tool rail.
TOOL_GROUPS = (
    (
        Tool("Select", "V", "Select or move objects. Shift-click adds to the selection.",
             (((5, 3), (5, 19), (9, 15), (12, 21), (15, 19), (12, 13), (18, 13), (5, 3)),)),
        Tool("Point Edit", "A", "Select a path and drag its points.",
             (((5, 17), (12, 6), (19, 15)),
              ((3, 16), (7, 16), (7, 20), (3, 20), (3, 16)),
              ((10, 4), (14, 4), (14, 8), (10, 8), (10, 4)),
              ((17, 14), (21, 14), (21, 18), (17, 18), (17, 14)))),
    ),
    (
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
        Tool("Polygon", "P", "Click to place corners. Press Enter to close the shape.",
             (((12, 4), (20, 10), (17, 19), (7, 19), (4, 10), (12, 4)),)),
        Tool("Freehand", "F", "Drag to draw a freehand path.",
             (((5, 19), (7, 13), (16, 4), (20, 8), (11, 17), (5, 19)),
              ((7, 13), (11, 17)), ((14, 6), (18, 10)))),
    ),
)
TOOLS = tuple(tool for group in TOOL_GROUPS for tool in group)
TOOL_NAMES = tuple(tool.name for tool in TOOLS)
TOOL_KEYS = {ord(tool.shortcut): index for index, tool in enumerate(TOOLS)}

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
}
