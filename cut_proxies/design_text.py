"""Resolve installed fonts with Pango and flatten their Cairo outlines."""

from contextlib import ExitStack
from ctypes import CDLL, POINTER, Structure, Union, byref, c_char_p, c_double, c_int, c_uint, c_void_p
from dataclasses import replace
from functools import lru_cache
import sys

from . import editor_model, vector_regions


class _PathHeader(Structure):
    _fields_ = [("kind", c_int), ("length", c_int)]


class _PathPoint(Structure):
    _fields_ = [("x", c_double), ("y", c_double)]


class _PathData(Union):
    _fields_ = [("header", _PathHeader), ("point", _PathPoint)]


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


def _flattened(text):
    lib = _library()
    scale = 16
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
        lib.cairo_set_tolerance(cr, 0.015 * scale)
        layout = own(lib.pango_cairo_create_layout, lib.g_object_unref, cr)
        description = own(lib.pango_font_description_new, lib.pango_font_description_free)
        lib.pango_font_description_set_family(description, text.family.encode())
        lib.pango_font_description_set_absolute_size(description, text.size_mm * scale * 1024)
        context = lib.pango_layout_get_context(layout)
        font = own(lib.pango_context_load_font, lib.g_object_unref, context, description)
        used = own(lib.pango_font_describe, lib.pango_font_description_free, font)
        actual = lib.pango_font_description_get_family(used)
        if not actual or actual.decode().casefold() != text.family.casefold():
            raise ValueError(f"Install {text.family} or select another font to edit this text.")
        lib.pango_layout_set_font_description(layout, description)
        attributes = own(lib.pango_attr_list_new, lib.pango_attr_list_unref)
        lib.pango_attr_list_insert(attributes, lib.pango_attr_fallback_new(0))
        lib.pango_layout_set_attributes(layout, attributes)
        lib.pango_layout_set_text(layout, text.value.encode(), -1)
        if lib.pango_layout_get_unknown_glyphs_count(layout):
            raise ValueError(f"{text.family} is missing characters in this text. Choose another font.")
        lib.pango_cairo_layout_path(cr, layout)
        path = own(lib.cairo_copy_path_flat, lib.cairo_path_destroy, cr).contents
        if path.status:
            raise ValueError("Could not read the font’s vector outlines.")
        i = 0
        rings, points = [], []
        while i < path.count:
            kind, length = path.data[i].header.kind, path.data[i].header.length
            if kind in (0, 1):  # move to, line to
                pt = path.data[i + 1].point
                if kind == 0:
                    points = []
                points.append((pt.x / scale, pt.y / scale))
            elif kind == 3 and len(set(points)) >= 3:  # close path
                rings.append(tuple(points))
            i += length
    return tuple(rings)


def shape(spec, x, y, original=None):
    if not editor_model.valid_text(spec):
        raise ValueError("Enter text (up to 2,048 characters), a font, and a size from 1 to 100 mm.")
    if spec.family.casefold() not in {family.casefold() for family in installed_families()}:
        raise ValueError(f"Install {spec.family} or select another font to edit this text.")
    if original is not None and spec == original.text:
        return original
    rings = _flattened(spec)
    if not rings:
        raise ValueError("This text contains no vector outlines. Try another font.")
    outlined = editor_model.compound_shape(vector_regions.outlines(rings), text=spec)
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
