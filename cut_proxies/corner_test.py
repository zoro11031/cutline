"""Find a clear corner and verify a tiny cut using the active job's coordinates."""

from dataclasses import asdict, dataclass, replace

from . import importer, job, template
from .geometry import Rect

SIZE_MM = 3.0
EDGE_MM = 1.0
CLEARANCE_MM = 1.0
SEARCH_MM = 24
CORNERS = (("bottom right", True, True), ("bottom left", False, True),
           ("top right", True, False), ("top left", False, False))


@dataclass(frozen=True)
class CornerTest:
    corner: str
    rect: Rect
    job: job.Job

    def describe(self):
        lines = [
            f"Cut one {SIZE_MM:g} mm square near the {self.corner} corner.",
            f"X {self.rect.x:g} mm, Y {self.rect.y:g} mm from the sheet's top-left.",
            "Keep this spot clear and leave the sheet loaded.",
        ]
        if self.job.registration:
            lines.append("The cutter will scan the printed marks first.")
            if not self.job.auto_search:
                lines.append("Position the cutter head over the first mark before starting.")
        return "\n".join(lines)


def _bounds(path):
    xs, ys = zip(*path.points)
    return Rect(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _within(inner, outer):
    return outer.x <= inner.x <= inner.x2 <= outer.x2 and outer.y <= inner.y <= inner.y2 <= outer.y2


def _find_spot(source, occupied):
    if source.registration:
        marks = source.marks
        window = Rect(*marks.origin_mm, marks.reg_width_mm, marks.reg_length_mm)
    else:
        window = Rect(0, 0, source.grid.sheet_w_mm, source.grid.sheet_h_mm)
    dx, dy = source.offset_mm
    offsets = sorted(((x, y) for x in range(2 * SEARCH_MM + 1) for y in range(2 * SEARCH_MM + 1)),
                     key=lambda p: (sum(p), max(p)))
    for shift_x, shift_y in offsets:
        for name, right, bottom in CORNERS:
            mx, my = EDGE_MM + shift_x / 2, EDGE_MM + shift_y / 2
            x = window.x2 - mx - SIZE_MM if right else window.x + mx
            y = window.y2 - my - SIZE_MM if bottom else window.y + my
            candidate = Rect(x, y, SIZE_MM, SIZE_MM)
            swept = Rect(x + min(0, dx), y + min(0, dy), SIZE_MM + abs(dx), SIZE_MM + abs(dy))
            if _within(swept, window) and all(not swept.overlaps(box) for box in occupied):
                return name, candidate
    raise ValueError("No clear corner fits a 3 mm test cut on this sheet.")


def build(source, artwork=None, design_shapes=(), bleed_mm=0):
    if source is None or source.blocked:
        raise ValueError("Review the current layout before running a corner test.")
    paths = list(artwork.paths) if artwork else []
    paths.extend(shape.path() for shape in design_shapes)
    card_boxes = (*source.grid.cards, *(_bounds(path) for path in paths if path.points))
    occupied = [box.grown(CLEARANCE_MM + bleed_mm) for box in card_boxes]
    if source.registration:
        occupied.extend(box.grown(CLEARANCE_MM) for box in source.marks.corner_zones())
        occupied.extend(box.grown(CLEARANCE_MM) for box in source.marks.bboxes())
    corner, area = _find_spot(source, occupied)
    path = template.CutPath(((area.x, area.y), (area.x2, area.y), (area.x2, area.y2), (area.x, area.y2)),
                            closed=True, label="Corner test")
    size = (source.grid.sheet_w_mm, source.grid.sheet_h_mm)
    imported = importer.Imported((path,), *size, "Corner test", "mm", page_size_mm=size,
                                 inkscape_marks=artwork.inkscape_marks if artwork else None)
    settings = replace(source.settings, passes=1, sharpen_corners=False, overcut_mm=0, track_enhancing=False)
    marks = source.marks
    checked = job.build(imported=imported, use_document_page=True, cutter=source.device.name,
                        preset_name=source.preset.name, blade_options=asdict(settings), pattern=marks.pattern,
                        mark_inset_mm=marks.inset_mm, mark_length_mm=marks.length_mm,
                        mark_thickness_mm=marks.thickness_mm, registration=source.registration,
                        auto_search=source.auto_search, offset_mm=source.offset_mm, mat=source.mat,
                        end_position="start")
    if checked.blocked or (source.registration and checked.marks != marks):
        raise ValueError("The corner test could not be verified with this job's cut window.")
    return CornerTest(corner, area, checked)
