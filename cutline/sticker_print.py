"""Export matched sticker artwork and checked cut geometry."""

from collections import deque
import json
import math
from pathlib import Path
import tempfile
import zipfile

from . import bundle, editor_model, job, regmarks, sticker_assets, sticker_pdf, template, vector_regions

_DPI = 300
_SCALE = _DPI / 25.4
_BLEED_SAMPLES = sorted(((x, y) for y in range(-3, 4) for x in range(-3, 4)),
                        key=lambda offset: offset[0] ** 2 + offset[1] ** 2)


def destination(path):
    if path.suffix.casefold() == ".zip":
        return path
    target = path.with_name(path.name + ".zip")
    if target.exists():
        raise ValueError(f"Choose {target.name} to confirm replacement.")
    return target


def _rings_mask(size, rings, left, top):
    from PIL import ImageChops, ImageDraw

    Image = sticker_assets.pillow()
    mask = Image.new("1", size)
    for ring in rings:
        part = Image.new("1", size)
        ImageDraw.Draw(part).polygon([(x * _SCALE - left, y * _SCALE - top) for x, y in ring], fill=1)
        mask = ImageChops.logical_xor(mask, part)
    return mask.convert("L")


def _extend_color(image, cut, outer):
    from PIL import ImageChops, ImageFilter

    # Grow the edge colors into the offset band. The vector offset defines
    # its physical width, independent of the artwork's resolution or scale.
    band = ImageChops.subtract(outer, cut)
    if not band.getbbox():
        return image
    original = image.load()
    fringe = band.load()
    boundary = ImageChops.subtract(cut, cut.filter(ImageFilter.MinFilter(3)))
    edge = boundary.load()
    bounds = boundary.getbbox()
    if bounds is None:
        return image
    x1, y1, x2, y2 = bounds
    width, height = image.size
    pending = deque()
    for y in range(y1, y2):
        for x in range(x1, x2):
            if not edge[x, y]:
                continue
            color = original[x, y]
            if color[3] < 255:
                # Prefer a nearby solid sample to repeating a faded, antialiased
                # edge across the full bleed band.
                color = max((original[x + dx, y + dy] for dx, dy in _BLEED_SAMPLES
                             if 0 <= x + dx < width and 0 <= y + dy < height), key=lambda pixel: pixel[3])
            if color[3]:
                pending.append((x, y, color))
    neighbours = ((-1, 0), (0, -1), (1, 0), (0, 1), (-1, -1), (1, -1), (1, 1), (-1, 1))
    for x, y, color in pending:
        original[x, y] = color
    while pending:
        x, y, color = pending.popleft()
        for dx, dy in neighbours:
            a, b = x + dx, y + dy
            if 0 <= a < width and 0 <= b < height and fringe[a, b]:
                original[a, b] = color
                fringe[a, b] = 0
                pending.append((a, b, color))
    return image


def stamp(shape):
    """Rasterize one transformed sticker in physical page space, with bleed."""
    from PIL import ImageChops

    Image = sticker_assets.pillow()
    image = shape.sticker.image()
    paths = tuple(path.points for path in shape.paths())
    outer = (vector_regions.modify("Offset", (paths,), distance=shape.sticker.bleed_mm)
             if paths and shape.sticker.bleed_mm else paths)
    frame = shape.frame().points
    xs, ys = zip(*(p for ring in (outer or (frame,)) for p in ring))
    left, top = math.floor(min(xs) * _SCALE) - 1, math.floor(min(ys) * _SCALE) - 1
    right, bottom = math.ceil(max(xs) * _SCALE) + 1, math.ceil(max(ys) * _SCALE) + 1
    size = right - left, bottom - top
    if math.prod(size) > 30_000_000:
        raise ValueError(f"{shape.sticker.name} is too large to print. Resize it on the page.")

    (x, y), (rx, ry), _, (bx, by) = frame
    ax, ay = rx - x, ry - y
    cx, cy = bx - x, by - y
    det = ax * cy - ay * cx
    u, v = left / _SCALE - x, top / _SCALE - y
    row_x = (image.width * cy / det, -image.width * cx / det)
    row_y = (-image.height * ay / det, image.height * ax / det)
    pixels = image.transform(size, Image.Transform.AFFINE,
                             (row_x[0] / _SCALE, row_x[1] / _SCALE, row_x[0] * u + row_x[1] * v,
                              row_y[0] / _SCALE, row_y[1] / _SCALE, row_y[0] * u + row_y[1] * v),
                             resample=Image.Resampling.BICUBIC)
    if paths:
        cut = _rings_mask(size, paths, left, top)
        pixels.putalpha(ImageChops.multiply(pixels.getchannel("A"), cut))
        if shape.sticker.bleed_mm:
            pixels = _extend_color(pixels, cut, _rings_mask(size, outer, left, top))
    return pixels, left / _SCALE, top / _SCALE, _SCALE


def check_print_area(name, placed, marks):
    """Refuse artwork that would print off-page or across a registration mark."""
    pixels, x, y, scale = placed
    alpha = pixels.getchannel("A")
    ink = alpha.getbbox()
    if not ink:
        raise ValueError(f"{name} has no printable pixels.")
    if (x + ink[0] / scale < -1 / scale or y + ink[1] / scale < -1 / scale
            or x + ink[2] / scale > marks.sheet_w_mm + 1 / scale
            or y + ink[3] / scale > marks.sheet_h_mm + 1 / scale):
        raise ValueError(f"Move or resize {name}. Its print or bleed extends off the page.")
    for zone in (*marks.corner_zones(), *marks.bboxes()):
        left, top = math.floor((zone.x - x) * scale), math.floor((zone.y - y) * scale)
        right, bottom = math.ceil((zone.x2 - x) * scale), math.ceil((zone.y2 - y) * scale)
        overlap = max(left, ink[0]), max(top, ink[1]), min(right, ink[2]), min(bottom, ink[3])
        if overlap[0] < overlap[2] and overlap[1] < overlap[3] and alpha.crop(overlap).getbbox():
            raise ValueError(f"Move {name} away from the registration marks or reduce its bleed.")


def export_readiness(project):
    """Return the visible stickers, first sticker to trace, and any prerequisite."""
    stickers = tuple(shape for shape in project.shapes if shape.visible and shape.sticker)
    if not stickers:
        return stickers, None, "Place a sticker image before exporting Print & Cut."
    for index, shape in enumerate(project.shapes):
        if shape.visible and shape.sticker and shape.action == "Cut" and not shape.contours:
            return stickers, index, f"Trace {shape.sticker.name} or set its action to None to print it without cutting."
    if not any(shape.action == "Cut" for shape in stickers):
        return stickers, None, "Set a traced sticker to Cut before exporting Print & Cut."
    return stickers, None, ""


def export(path, project, *, cutter):
    stickers, _, prerequisite = export_readiness(project)
    if prerequisite:
        raise ValueError(prerequisite)
    registration = project.registration or editor_model.Registration.from_marks(regmarks.marks(project.width, project.height))
    drawing = project.artwork()
    plan = job.build(cutter=cutter, imported=drawing, use_document_page=True, registration=True, mat=project.mat,
                     pattern=registration.pattern, mark_inset_mm=registration.inset_mm,
                     mark_length_mm=registration.length_mm, mark_thickness_mm=registration.thickness_mm,
                     offset_mm=(0, 0))
    if plan.blocked:
        raise ValueError("Fix these cut checks before exporting.\n" + "\n".join(plan.blocked[:4]))
    marks = plan.marks
    orientation = "portrait" if project.width <= project.height else "landscape"
    size = dict(width=project.width, height=project.height, orientation=orientation)
    manifest = {"format": bundle.STICKER_FORMAT, "version": 1, "units": "mm", "origin": "top left",
                "pdf": "print.pdf", "pdfPage": size, "mat": size, "cuttingMat": project.mat,
                "registration": dict(inset=marks.inset_mm, armLength=marks.length_mm,
                                     thickness=marks.thickness_mm, pattern=3 if marks.square else 4),
                "sheets": [dict(sheet=1, frontPage=1, backPage=None, cards=len(drawing.paths), svg="sheet-1.svg")]}

    def print_images():
        for shape in stickers:
            placed = stamp(shape)
            check_print_area(shape.sticker.name, placed, marks)
            yield placed

    target = Path(path)
    with tempfile.TemporaryDirectory(dir=target.parent) as folder:
        folder = Path(folder)
        pdf = folder / manifest["pdf"]
        sticker_pdf.write(pdf, marks, print_images())
        cut_svg = template.write_svg(None, plan.grid, drawing.paths)
        archive = folder / "matched.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("registration.json", json.dumps(manifest, indent=2) + "\n")
            output.write(pdf, manifest["pdf"])
            output.writestr("sheet-1.svg", cut_svg)
        # Check the written geometry through the same entry point used in Send.
        loaded = bundle.load(archive)
        loaded.artwork(loaded.row())
        archive.replace(target)
    return target
