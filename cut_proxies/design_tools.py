"""Tool names, shortcuts and 24-unit icon outlines shared by both editors."""

from dataclasses import dataclass

from .editor_model import KINDS


@dataclass(frozen=True)
class Tool:
    name: str
    shortcut: str
    hint: str
    strokes: tuple[tuple[tuple[float, float], ...], ...]

    @property
    def tooltip(self):
        return f"{self.name} ({self.shortcut})\n{self.hint}"


RECTANGLE, ROUNDED_RECTANGLE, ELLIPSE, LINE = KINDS

# The first row is for editing. The next two rows draw on the canvas.
TOOL_ROWS = (
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
    ),
    (
        Tool(LINE, "L", "Drag between the endpoints of a straight line.",
             (((5, 19), (19, 5)),)),
        Tool("Polygon", "P", "Click to place corners. Press Enter to close the shape.",
             (((12, 4), (20, 10), (17, 19), (7, 19), (4, 10), (12, 4)),)),
        Tool("Freehand", "F", "Drag to draw a freehand path.",
             (((5, 19), (7, 13), (16, 4), (20, 8), (11, 17), (5, 19)),
              ((7, 13), (11, 17)), ((14, 6), (18, 10)))),
    ),
)
TOOLS = tuple(tool for row in TOOL_ROWS for tool in row)
TOOL_NAMES = tuple(tool.name for tool in TOOLS)
TOOL_KEYS = {ord(tool.shortcut): index for index, tool in enumerate(TOOLS)}
