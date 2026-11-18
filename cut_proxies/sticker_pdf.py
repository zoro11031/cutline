"""Write raster artwork and vector registration marks at exact PDF dimensions."""

from contextlib import ExitStack
from ctypes import CDLL, c_char_p, c_double, c_int, c_void_p
from functools import lru_cache
import os
from pathlib import Path
from tempfile import TemporaryDirectory


@lru_cache(maxsize=1)
def _cairo():
    try:
        lib = CDLL("libcairo.so.2")
        p, d = c_void_p, c_double
        functions = {
            "pdf_surface_create": (p, c_char_p, d, d), "surface_destroy": (None, p),
            "surface_finish": (None, p), "surface_status": (c_int, p),
            "image_surface_create_from_png": (p, c_char_p), "create": (p, p), "destroy": (None, p),
            "status": (c_int, p), "status_to_string": (c_char_p, c_int), "show_page": (None, p),
            "scale": (None, p, d, d), "translate": (None, p, d, d), "save": (None, p), "restore": (None, p),
            "set_source_surface": (None, p, p, d, d), "paint": (None, p), "set_source_rgb": (None, p, d, d, d),
            "rectangle": (None, p, d, d, d, d), "fill": (None, p), "set_line_width": (None, p, d),
            "move_to": (None, p, d, d), "line_to": (None, p, d, d), "stroke": (None, p),
        }
        for name, (result, *arguments) in functions.items():
            function = getattr(lib, f"cairo_{name}")
            function.restype, function.argtypes = result, arguments
    except (OSError, AttributeError) as error:
        raise ValueError("Install Cairo with PDF support to export the print sheet.") from error
    return lib


def write(path, marks, images):
    lib = _cairo()
    points_per_mm = 72 / 25.4
    with ExitStack() as stack, TemporaryDirectory() as folder:
        def check(status):
            if status:
                raise ValueError(f"Could not create the print PDF: {lib.cairo_status_to_string(status).decode()}.")

        page = lib.cairo_pdf_surface_create(os.fsencode(path), marks.sheet_w_mm * points_per_mm,
                                           marks.sheet_h_mm * points_per_mm)
        stack.callback(lib.cairo_surface_destroy, page)
        check(lib.cairo_surface_status(page))
        cr = lib.cairo_create(page)
        stack.callback(lib.cairo_destroy, cr)
        check(lib.cairo_status(cr))
        lib.cairo_scale(cr, points_per_mm, points_per_mm)
        png = os.fsencode(Path(folder) / "sticker.png")
        for image, x, y, pixels_per_mm in images:
            image.save(png, "PNG")
            source = lib.cairo_image_surface_create_from_png(png)
            try:
                check(lib.cairo_surface_status(source))
                lib.cairo_save(cr)
                lib.cairo_translate(cr, x, y)
                lib.cairo_scale(cr, 1 / pixels_per_mm, 1 / pixels_per_mm)
                lib.cairo_set_source_surface(cr, source, 0, 0)
                lib.cairo_paint(cr)
                lib.cairo_restore(cr)
            finally:
                lib.cairo_surface_destroy(source)

        lib.cairo_set_source_rgb(cr, 0, 0, 0)
        if marks.square:
            box = marks.square
            lib.cairo_rectangle(cr, box.x, box.y, box.w, box.h)
            lib.cairo_fill(cr)
        lib.cairo_set_line_width(cr, marks.thickness_mm)
        for ell in marks.ells:
            for x1, y1, x2, y2 in ell.arms():
                lib.cairo_move_to(cr, x1, y1)
                lib.cairo_line_to(cr, x2, y2)
        lib.cairo_stroke(cr)
        lib.cairo_show_page(cr)
        check(lib.cairo_status(cr))
        lib.cairo_surface_finish(page)
        check(lib.cairo_surface_status(page))
