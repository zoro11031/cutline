"""Resolve installed fonts with Pango and flatten their Cairo outlines."""

from contextlib import ExitStack
from ctypes import CDLL, POINTER, Structure, Union, byref, c_char_p, c_double, c_float, c_int, c_uint, c_void_p
from dataclasses import replace
from functools import lru_cache
import math
import sys

from . import editor_model, vector_regions


class _PathHeader(Structure):
    _fields_ = [("kind", c_int), ("length", c_int)]


class _PathPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class _PathData(Union):
    _fields_ = [("header", _PathHeader), ("point", _PathPoint)]


class _PangoRectangle(Structure):
    _fields_ = [("x", c_int), ("y", c_int), ("width", c_int), ("height", c_int)]


class _CairoPath(Structure):
    _fields_ = [("status", c_int), ("data", POINTER(_PathData)), ("count", c_int)]


class _WindowsTextLibraries:
    # Windows resolves symbols from the exporting DLL, not its dependencies.
    def __init__(self):
        self.libraries = {prefix: CDLL(name) for prefix, name in (
            ("cairo_", "libcairo-2.dll"),
            ("pango_cairo_", "libpangocairo-1.0-0.dll"),
            ("pango_", "libpango-1.0-0.dll"),
            ("g_object_", "libgobject-2.0-0.dll"),
            ("g_", "libglib-2.0-0.dll"),
        )}

    def __getattr__(self, name):
        for prefix, library in self.libraries.items():
            if name.startswith(prefix):
                return getattr(library, name)
        raise AttributeError(name)


@lru_cache(maxsize=1)
def _library():
    p = c_void_p
    try:
        # PangoCairo links all three ABI libraries used below. Accessing their
        # exported APIs through this handle also works with bundled libraries.
        lib = (_WindowsTextLibraries() if sys.platform == "win32"
               else CDLL("libpangocairo-1.0.so.0"))
        declarations = {
            "cairo_recording_surface_create": (p, c_int, p), "cairo_surface_destroy": (None, p),
            "cairo_create": (p, p), "cairo_destroy": (None, p), "cairo_set_tolerance": (None, p, c_double),
            "cairo_font_options_create": (p,), "cairo_font_options_destroy": (None, p),
            "cairo_font_options_set_hint_style": (None, p, c_int),
            "cairo_font_options_set_hint_metrics": (None, p, c_int),
            "cairo_set_font_options": (None, p, p), "cairo_copy_path_flat": (POINTER(_CairoPath), p),
            "cairo_path_destroy": (None, POINTER(_CairoPath)),
            "pango_cairo_font_map_get_default": (p,),
            "pango_font_map_list_families": (None, p, POINTER(POINTER(p)), POINTER(c_int)),
            "pango_font_family_get_name": (c_char_p, p), "g_free": (None, p), "g_object_unref": (None, p),
            "pango_cairo_create_layout": (p, p), "pango_cairo_layout_path": (None, p, p),
            "pango_layout_get_context": (p, p), "pango_context_load_font": (p, p, p),
            "pango_font_describe": (p, p), "pango_font_description_get_family": (c_char_p, p),
            "pango_font_description_new": (p,), "pango_font_description_free": (None, p),
            "pango_font_description_set_family": (None, p, c_char_p),
            "pango_font_description_set_absolute_size": (None, p, c_double),
            "pango_layout_set_font_description": (None, p, p), "pango_layout_set_text": (None, p, c_char_p, c_int),
            "pango_attr_list_new": (p,), "pango_attr_list_unref": (None, p),
            "pango_attr_fallback_new": (p, c_int), "pango_attr_list_insert": (None, p, p),
            "pango_layout_set_attributes": (None, p, p), "pango_layout_get_unknown_glyphs_count": (c_int, p),
            # Studio's Text Style options (manual chapter 05).
            "pango_font_description_set_weight": (None, p, c_int),
            "pango_font_description_set_style": (None, p, c_int),
            "pango_layout_set_alignment": (None, p, c_int), "pango_layout_set_width": (None, p, c_int),
            "pango_layout_set_wrap": (None, p, c_int), "pango_layout_set_line_spacing": (None, p, c_float),
            "pango_attr_letter_spacing_new": (p, c_int), "pango_layout_get_baseline": (c_int, p),
            "pango_layout_get_extents": (None, p, POINTER(_PangoRectangle), POINTER(_PangoRectangle)),
            "cairo_new_path": (None, p),
        }
        for name, (result, *arguments) in declarations.items():
            function = getattr(lib, name)
            function.restype, function.argtypes = result, arguments
    except (OSError, AttributeError) as error:
        raise ValueError("Install Pango and Cairo to edit text.") from error
    return lib


def installed_families():
    lib = _library()
    array, count = POINTER(c_void_p)(), c_int()
    lib.pango_font_map_list_families(lib.pango_cairo_font_map_get_default(), byref(array), byref(count))
    try:
        aliases = {"sans", "sans-serif", "serif", "monospace", "system-ui"}
        names = {lib.pango_font_family_get_name(array[i]).decode() for i in range(count.value)}
        return tuple(sorted((name for name in names if name.casefold() not in aliases), key=str.casefold))
    finally:
        lib.g_free(array)


def unavailable_fonts(project):
    families = {s.text.family for s in project.shapes if s.text is not None}
    if not families:
        return ""
    try:
        available = {family.casefold() for family in installed_families()}
    except ValueError:
        available = set()
    missing = sorted(family for family in families if family.casefold() not in available)
    return f"Missing fonts: {', '.join(missing)}. Using the saved cut outlines." if missing else ""


def font_choices(spec):
    families = installed_families()
    if not families:
        raise ValueError("Install a font to add editable text.")
    if spec is None:
        return families, "DejaVu Sans" if "DejaVu Sans" in families else families[0], ""
    matching = next((family for family in families if family.casefold() == spec.family.casefold()), None)
    if matching:
        return families, matching, ""
    return (spec.family, *families), spec.family, f"{spec.family} is missing. Select an installed font to edit."


SCALE = 16  # outline at 16x and divide, for smoother curves from Cairo's flattening


def _layout(text, string, stack, lib, cr):
    """A Pango layout of `string` in `text`'s style, checked for missing glyphs."""
    def own(allocate, release, *args):
        handle = allocate(*args)
        if not handle:
            raise ValueError("Could not prepare the text outlines.")
        stack.callback(release, handle)
        return handle

    layout = own(lib.pango_cairo_create_layout, lib.g_object_unref, cr)
    description = own(lib.pango_font_description_new, lib.pango_font_description_free)
    lib.pango_font_description_set_family(description, text.family.encode())
    lib.pango_font_description_set_absolute_size(description, text.size_mm * SCALE * 1024)
    lib.pango_font_description_set_weight(description, 700 if text.bold else 400)
    lib.pango_font_description_set_style(description, 2 if text.italic else 0)
    context = lib.pango_layout_get_context(layout)
    font = own(lib.pango_context_load_font, lib.g_object_unref, context, description)
    used = own(lib.pango_font_describe, lib.pango_font_description_free, font)
    actual = lib.pango_font_description_get_family(used)
    if not actual or actual.decode().casefold() != text.family.casefold():
        raise ValueError(f"Install {text.family} or select another font to edit this text.")
    lib.pango_layout_set_font_description(layout, description)
    attributes = own(lib.pango_attr_list_new, lib.pango_attr_list_unref)
    lib.pango_attr_list_insert(attributes, lib.pango_attr_fallback_new(0))
    if text.char_spacing != 100:
        extra = (text.char_spacing - 100) / 100 * text.size_mm * SCALE * 1024
        lib.pango_attr_list_insert(attributes, lib.pango_attr_letter_spacing_new(round(extra)))
    lib.pango_layout_set_attributes(layout, attributes)
    lib.pango_layout_set_alignment(layout, editor_model.TEXT_ALIGNMENTS.index(text.align))
    if text.wrap_mm > 0:
        lib.pango_layout_set_width(layout, round(text.wrap_mm * SCALE * 1024))
        lib.pango_layout_set_wrap(layout, 0)
    if text.line_spacing != 100:
        lib.pango_layout_set_line_spacing(layout, text.line_spacing / 100)
    lib.pango_layout_set_text(layout, string.encode(), -1)
    if lib.pango_layout_get_unknown_glyphs_count(layout):
        raise ValueError(f"{text.family} is missing characters in this text. Choose another font.")
    return layout


def _rings(lib, cr, layout):
    lib.cairo_new_path(cr)
    lib.pango_cairo_layout_path(cr, layout)
    path = lib.cairo_copy_path_flat(cr)
    try:
        if path.contents.status:
            raise ValueError("Could not read the font’s vector outlines.")
        data, count = path.contents.data, path.contents.count
        i = 0
        rings, points = [], []
        while i < count:
            kind, length = data[i].header.kind, data[i].header.length
            if kind in (0, 1):  # move to, line to
                pt = data[i + 1].point
                if kind == 0:
                    points = []
                points.append((pt.x / SCALE, pt.y / SCALE))
            elif kind == 3 and len(set(points)) >= 3:  # close path
                rings.append(tuple(points))
            i += length
        return tuple(rings)
    finally:
        lib.cairo_path_destroy(path)


def _with_canvas(work):
    lib = _library()
    with ExitStack() as stack:
        def own(allocate, release, *args):
            handle = allocate(*args)
            if not handle:
                raise ValueError("Could not prepare the text outlines.")
            stack.callback(release, handle)
            return handle

        surface = own(lib.cairo_recording_surface_create, lib.cairo_surface_destroy, 0x3000, None)
        cr = own(lib.cairo_create, lib.cairo_destroy, surface)
        options = own(lib.cairo_font_options_create, lib.cairo_font_options_destroy)
        lib.cairo_font_options_set_hint_style(options, 1)
        lib.cairo_font_options_set_hint_metrics(options, 1)
        lib.cairo_set_font_options(cr, options)
        lib.cairo_set_tolerance(cr, 0.015 * SCALE)
        return work(lib, cr, stack)


def _flattened(text):
    """The text's outlines in mm, laid out as a block or along its guide path."""
    if text.path:
        return _along_path(text)
    return _with_canvas(lambda lib, cr, stack: _rings(lib, cr, _layout(text, text.value, stack, lib, cr)))


def _along_path(text):
    """Studio's Text to Path: each character's outline set on the guide line,
    centred on its advance and turned to the line's direction. Letters are
    laid out one at a time, so pair kerning between them is not applied."""
    guide = list(text.path)
    lengths = [0.0]
    for a, b in zip(guide, guide[1:]):
        lengths.append(lengths[-1] + math.dist(a, b))

    def place(distance):
        if distance <= 0 or len(guide) < 2:
            i = 0
        else:
            i = next((k for k in range(len(guide) - 1) if lengths[k + 1] >= distance), len(guide) - 2)
        a, b = guide[i], guide[i + 1]
        span = math.dist(a, b) or 1
        t = (distance - lengths[i]) / span
        return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t), math.atan2(b[1] - a[1], b[0] - a[0])

    def work(lib, cr, stack):
        rings = []
        cursor = text.path_start
        spacing = (text.char_spacing - 100) / 100 * text.size_mm
        for character in text.value.replace("\n", " "):
            layout = _layout(replace(text, char_spacing=100.0, wrap_mm=0.0), character, stack, lib, cr)
            logical = _PangoRectangle()
            lib.pango_layout_get_extents(layout, None, byref(logical))
            advance = logical.width / 1024 / SCALE
            baseline = lib.pango_layout_get_baseline(layout) / 1024 / SCALE
            (px, py), angle = place(cursor + advance / 2)
            c, s_ = math.cos(angle), math.sin(angle)
            for ring in _rings(lib, cr, layout):
                moved = []
                for x, y in ring:
                    lx, ly = x - advance / 2, y - baseline - text.path_offset
                    moved.append((px + c * lx - s_ * ly, py + s_ * lx + c * ly))
                rings.append(tuple(moved))
            cursor += advance + spacing
        return tuple(rings)

    return _with_canvas(work)


def shape(spec, x, y, original=None):
    if not editor_model.valid_text(spec):
        raise ValueError("Enter text (up to 2,048 characters), a font, a size from 1 to 100 mm, and spacing "
                         "from 10% to 500%.")
    if spec.family.casefold() not in {family.casefold() for family in installed_families()}:
        raise ValueError(f"Install {spec.family} or select another font to edit this text.")
    if original is not None and spec == original.text:
        return original
    rings = _flattened(spec)
    if not rings:
        raise ValueError("This text contains no vector outlines. Try another font.")
    outlined = editor_model.compound_shape(vector_regions.outlines(rings), text=spec)
    if spec.path:
        # Text on a path sits where its guide line is.
        if original is None:
            return outlined
        return replace(original, x=outlined.x, y=outlined.y, width=outlined.width, height=outlined.height,
                       rotation=0, flip_x=False, flip_y=False, contours=outlined.contours, text=spec)
    if original is None:
        return replace(outlined, x=x, y=y)
    return replace(original, width=outlined.width, height=outlined.height, contours=outlined.contours, text=spec)


def edit(project, spec, index=None):
    shapes = project.shapes
    if index is None:
        outlined = shape(spec, 0, 0)
        new = replace(outlined, x=max(0, (project.width - outlined.width) / 2),
                      y=max(0, (project.height - outlined.height) / 2))
        return (*shapes, new), (len(shapes),)
    updated = shape(spec, 0, 0, shapes[index])
    return (*shapes[:index], updated, *shapes[index + 1:]), (index,)
