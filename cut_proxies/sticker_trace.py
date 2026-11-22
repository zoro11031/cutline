"""Trace sticker contours in the image's local coordinates."""

from dataclasses import replace

from . import editor_model as model, sticker_assets


def place(path, page_width, page_height):
    sticker, (width, height) = sticker_assets.load(path)
    scale = min(1, page_width * 0.65 / width, page_height * 0.65 / height)
    width, height = max(0.1, width * scale), max(0.1, height * scale)
    return model.Shape("Sticker", (page_width - width) / 2, (page_height - height) / 2,
                       width, height, sticker=sticker)


def trace(shape, *, source, threshold, keep_holes, bleed_mm):
    from PIL import ImageOps

    Image = sticker_assets.pillow()
    original = shape.sticker.image()
    image = original.copy()
    image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
    alpha = image.getchannel("A")
    if source == "Transparency" or (source == "Automatic" and alpha.getextrema()[0] < 255):
        coverage = alpha
    else:
        background = 0 if source == "Light artwork" else 255
        gray = Image.composite(ImageOps.grayscale(image), Image.new("L", image.size, background), alpha)
        coverage = gray if source == "Light artwork" else ImageOps.invert(gray)
    mask = coverage.point([0 if value < threshold else 255 for value in range(256)])
    rings = _outline(mask, keep_holes)
    if not rings:
        raise ValueError("No artwork matches this threshold. Adjust it or choose a different background.")
    settings = replace(shape.sticker, trace_source=source, threshold=threshold,
                       keep_holes=keep_holes, bleed_mm=bleed_mm)
    settings.validate()
    return replace(shape, contours=rings, sticker=settings)


# Marching squares, clockwise around foreground in image coordinates. At a
# diagonal contact, each foreground pixel keeps its own boundary.
_SEGMENTS = (
    (), ((3, 2),), ((2, 1),), ((3, 1),), ((1, 0),), ((1, 0), (3, 2)), ((2, 0),), ((3, 0),),
    ((0, 3),), ((0, 2),), ((0, 3), (2, 1)), ((0, 1),), ((1, 3),), ((1, 2),), ((2, 3),), (),
)


def _outline(mask, keep_holes):
    Image = sticker_assets.pillow()
    width, height = mask.size
    crop = mask.getbbox()
    if crop is None:
        return ()
    left, top, right, bottom = crop
    stride = right - left + 2
    padded = Image.new("L", (stride, bottom - top + 2))
    padded.paste(mask.crop(crop), (1, 1))
    pixels = padded.tobytes()
    links = {}
    for y in range(padded.height - 1):
        row = y * stride
        for x in range(stride - 1):
            here = row + x
            case = (8 * bool(pixels[here]) + 4 * bool(pixels[here + 1])
                    + 2 * bool(pixels[here + stride + 1]) + bool(pixels[here + stride]))
            if not _SEGMENTS[case]:
                continue
            u, v = (x + left) * 2, (y + top) * 2
            edges = ((u, v - 1), (u + 1, v), (u, v + 1), (u - 1, v))
            for a, b in _SEGMENTS[case]:
                links[edges[a]] = edges[b]
    rings = []
    while links:
        first = next(iter(links))
        points = [first]
        current = links.pop(first)
        while current != first:
            points.append(current)
            current = links.pop(current)
        area = sum(x * b - y * a for (x, y), (a, b) in zip(points, points[1:] + points[:1]))
        if keep_holes or area > 0:
            corners = [point for index, point in enumerate(points)
                       if _corner(points[index - 1], point, points[(index + 1) % len(points)])]
            rings.append(tuple((x / (2 * width), y / (2 * height)) for x, y in corners))
    return tuple(rings)


def _corner(a, b, c):
    return (b[0] - a[0]) * (c[1] - b[1]) != (b[1] - a[1]) * (c[0] - b[0])


# ---- Studio's Trace panel (user manual chapter 06) ----------------------------

def trace_mask(shape, *, area=None, threshold=128, high_pass=False, smoothing=0.0):
    """The part of a placed image that tracing would outline, as a 0/255 mask
    at the working resolution. `area` is (left, top, right, bottom) in 0..1 of
    the image, as Studio's Select Trace Area drag; None traces all of it."""
    from PIL import ImageChops, ImageFilter, ImageOps

    Image = sticker_assets.pillow()
    image = shape.sticker.image().copy()
    image.thumbnail((1000, 1000), Image.Resampling.LANCZOS)
    alpha = image.getchannel("A")
    gray = Image.composite(ImageOps.grayscale(image), Image.new("L", image.size, 255), alpha)
    if smoothing > 0:
        gray = gray.filter(ImageFilter.GaussianBlur(smoothing))
    if high_pass:
        # Edges: how far each pixel differs from its neighbourhood.
        coverage = ImageOps.autocontrast(ImageChops.difference(gray, gray.filter(ImageFilter.GaussianBlur(4))))
    else:
        coverage = ImageOps.invert(gray)
    mask = coverage.point([0 if value < 256 - threshold else 255 for value in range(256)])
    if area is not None:
        left, top, right, bottom = area
        box = (round(left * mask.width), round(top * mask.height), round(right * mask.width),
               round(bottom * mask.height))
        if box[2] - box[0] < 2 or box[3] - box[1] < 2:
            raise ValueError("Drag a larger trace area over the image.")
        keep = Image.new("L", mask.size, 0)
        keep.paste(mask.crop(box), box[:2])
        mask = keep
    return mask


def trace_shapes(shape, *, area=None, threshold=128, high_pass=False, smoothing=0.0, style="Trace"):
    """Cut lines traced from a placed image, on the page: Studio's Trace (every
    outline), Trace Outer Edge (outer outlines only) and Trace and Detach
    (Trace, plus the image cut away from its background)."""
    if style not in ("Trace", "Trace Outer Edge", "Trace and Detach"):
        raise ValueError(f"Unknown trace style {style!r}.")
    mask = trace_mask(shape, area=area, threshold=threshold, high_pass=high_pass, smoothing=smoothing)
    rings = _outline(mask, style != "Trace Outer Edge")
    if not rings:
        raise ValueError("Nothing in the trace area passes the threshold. Adjust the threshold or the area.")
    frame = shape.frame().points
    (x0, y0), (x1, y1), _, (x3, y3) = frame
    place = lambda u, v: (x0 + (x1 - x0) * u + (x3 - x0) * v, y0 + (y1 - y0) * u + (y3 - y0) * v)  # noqa: E731
    page_rings = tuple(tuple(place(u, v) for u, v in ring) for ring in rings)
    if style == "Trace Outer Edge":
        traced = [model.shape_from_path(model.template.CutPath(ring, True)) for ring in page_rings]
    else:
        traced = [model.compound_shape(page_rings) if len(page_rings) > 1
                  else model.shape_from_path(model.template.CutPath(page_rings[0], True))]
    image = shape
    if style == "Trace and Detach":
        Image = sticker_assets.pillow()
        original = shape.sticker.image()
        cut_out = original.copy()
        alpha = Image.composite(original.getchannel("A"), Image.new("L", original.size, 0),
                                mask.resize(original.size, Image.Resampling.NEAREST))
        cut_out.putalpha(alpha)
        data = __import__("io").BytesIO()
        cut_out.save(data, "PNG")
        sticker = replace(shape.sticker, png=__import__("base64").b64encode(data.getvalue()).decode("ascii"))
        sticker.validate()
        image = replace(shape, sticker=sticker)
    return image, tuple(traced)
