"""Filled path operations in millimetres, using the GEOS C API."""

from contextlib import ExitStack
from ctypes import CDLL, POINTER, byref, c_char_p, c_double, c_int, c_uint, c_void_p, cast
from functools import lru_cache
import json
import math
import os


@lru_cache(maxsize=1)
def _library():
    try:
        candidates = ["libgeos_c.so.1", "libgeos_c.so"]
        user_brew = os.path.expanduser("~/.linuxbrew/lib/libgeos_c.so.1")
        if os.path.exists(user_brew):
            candidates.append(user_brew)
        if os.path.exists("/home/linuxbrew/.linuxbrew/lib/libgeos_c.so.1"):
            candidates.append("/home/linuxbrew/.linuxbrew/lib/libgeos_c.so.1")
        lib = None
        for candidate in candidates:
            try:
                lib = CDLL(candidate)
                break
            except OSError:
                continue
        if lib is None:
            raise OSError("libgeos_c not found")
        pointer = c_void_p
        signatures = {
            "GEOS_init": (pointer,), "GEOS_finish": (None, pointer),
            "GEOSGeoJSONReader_create": (pointer, pointer),
            "GEOSGeoJSONReader_destroy": (None, pointer, pointer),
            "GEOSGeoJSONReader_readGeometry": (pointer, pointer, pointer, c_char_p),
            "GEOSGeoJSONWriter_create": (pointer, pointer),
            "GEOSGeoJSONWriter_destroy": (None, pointer, pointer),
            "GEOSGeoJSONWriter_writeGeometry": (pointer, pointer, pointer, pointer, c_int),
            "GEOSGeom_destroy": (None, pointer, pointer), "GEOSFree": (None, pointer, pointer),
            "GEOSUnaryUnion": (pointer, pointer, pointer),
            "GEOSPolygonize": (pointer, pointer, POINTER(pointer), c_uint),
            "GEOSPointOnSurface": (pointer, pointer, pointer),
            "GEOSGetNumGeometries": (c_int, pointer, pointer),
            "GEOSGetGeometryN": (pointer, pointer, pointer, c_int),
            "GEOSGeomGetX": (c_int, pointer, pointer, POINTER(c_double)),
            "GEOSGeomGetY": (c_int, pointer, pointer, POINTER(c_double)),
            "GEOSDifference": (pointer, pointer, pointer, pointer),
            "GEOSIntersection": (pointer, pointer, pointer, pointer),
            "GEOSBufferWithStyle": (pointer, pointer, pointer, c_double, c_int, c_int, c_int, c_double),
        }
        for name, (result, *arguments) in signatures.items():
            function = getattr(lib, f"{name}_r")
            function.restype, function.argtypes = result, arguments
    except (OSError, AttributeError) as error:
        raise ValueError("Install GEOS 3.10 or newer to use filled path tools.") from error
    return lib


class _RegionContext:
    def __init__(self, stack):
        self.stack = stack
        self.lib = _library()
        self.handle = self.lib.GEOS_init_r()
        if not self.handle:
            raise ValueError("Could not initialize the geometry engine.")
        stack.callback(self.lib.GEOS_finish_r, self.handle)
        self.reader = self.own("GEOSGeoJSONReader_create", "GEOSGeoJSONReader_destroy")
        self.writer = self.own("GEOSGeoJSONWriter_create", "GEOSGeoJSONWriter_destroy")

    def call(self, name, *args):
        return getattr(self.lib, f"{name}_r")(self.handle, *args)

    def own(self, create, destroy, *args, stack=None):
        result = self.call(create, *args)
        if not result:
            raise ValueError("The geometry operation could not be completed.")
        (stack if stack is not None else self.stack).callback(self.call, destroy, result)
        return result

    def geom(self, name, *args, stack=None):
        return self.own(name, "GEOSGeom_destroy", *args, stack=stack)

    def read(self, kind, coordinates):
        data = json.dumps({"type": kind, "coordinates": coordinates}, allow_nan=False).encode()
        return self.geom("GEOSGeoJSONReader_readGeometry", self.reader, data)

    def write(self, shape):
        with ExitStack() as stack:
            data = self.own("GEOSGeoJSONWriter_writeGeometry", "GEOSFree", self.writer, shape, -1, stack=stack)
            return json.loads(cast(data, c_char_p).value)

    def region(self, paths, *, nonzero=False):
        # Node intersections before polygonizing so nested, touching and crossed
        # contours each contribute according to the same fill rule as the canvas.
        rings = [_closed(points) for points in paths]
        lines = self.read("MultiLineString", rings)
        noded = self.geom("GEOSUnaryUnion", lines)
        faces = self.geom("GEOSPolygonize", (c_void_p * 1)(noded), 1)
        bounds = [(min(x for x, _ in ring), min(y for _, y in ring),
                   max(x for x, _ in ring), max(y for _, y in ring)) for ring in rings]
        polygons = []
        for i in range(self.call("GEOSGetNumGeometries", faces)):
            face = self.call("GEOSGetGeometryN", faces, i)
            with ExitStack() as stack:
                point = self.geom("GEOSPointOnSurface", face, stack=stack)
                x, y = c_double(), c_double()
                self.call("GEOSGeomGetX", point, byref(x))
                self.call("GEOSGeomGetY", point, byref(y))
            winding = sum(_winding(ring, x.value, y.value) for ring, (l, t, r, b) in zip(rings, bounds)
                          if l <= x.value <= r and t <= y.value <= b)
            if (winding != 0 if nonzero else winding % 2):
                polygons.append(self.write(face)["coordinates"])
        # Polygonizer faces can touch along edges. Union them as a collection
        # of polygons so no invalid intermediate MultiPolygon is required.
        data = json.dumps({"type": "GeometryCollection", "geometries": [
            {"type": "Polygon", "coordinates": polygon} for polygon in polygons]}).encode()
        collection = self.geom("GEOSGeoJSONReader_readGeometry", self.reader, data)
        return self.geom("GEOSUnaryUnion", collection)


def _closed(points):
    points = tuple(points)
    if len(set(points)) < 3 or not all(math.isfinite(v) for point in points for v in point):
        raise ValueError("Choose closed contours with at least three distinct points.")
    return points if points[0] == points[-1] else (*points, points[0])


def _winding(ring, x, y):
    winding = 0
    for (ax, ay), (bx, by) in zip(ring, ring[1:]):
        if ay <= y < by or by <= y < ay:
            crossing = ax + (y - ay) * (bx - ax) / (by - ay)
            if x < crossing:
                winding += 1 if ay < by else -1
    return winding


def _contours(data):
    kind = data["type"]
    if kind == "GeometryCollection":
        return tuple(ring for part in data["geometries"] for ring in _contours(part))
    if kind == "MultiPolygon":
        return tuple(tuple(map(tuple, ring[:-1])) for polygon in data["coordinates"] for ring in polygon if ring)
    if kind == "Polygon":
        return tuple(tuple(map(tuple, ring[:-1])) for ring in data["coordinates"] if ring)
    return ()


def outlines(paths, *, nonzero=True):
    """Resolve filled outlines. Fonts use nonzero winding and compound paths use odd-even fill."""
    with ExitStack() as stack:
        context = _RegionContext(stack)
        return _contours(context.write(context.region(paths, nonzero=nonzero)))


def modify(operation, shapes, *, distance=0):
    with ExitStack() as stack:
        context = _RegionContext(stack)
        regions = [context.region(contours) for contours in shapes]
        if operation == "Weld":
            # The inputs already encode holes. Union them rather than toggling
            # areas covered by more than one object.
            data = json.dumps({"type": "GeometryCollection", "geometries": list(map(context.write, regions))}).encode()
            collection = context.geom("GEOSGeoJSONReader_readGeometry", context.reader, data)
            result = context.geom("GEOSUnaryUnion", collection)
        elif operation == "Offset":
            result = context.geom("GEOSBufferWithStyle", regions[0], distance, 8, 1, 2, 5.0)
        else:
            function = {"Subtract": "GEOSDifference", "Intersect": "GEOSIntersection"}[operation]
            result = regions[0]
            for region in regions[1:]:
                result = context.geom(function, result, region)
        return _contours(context.write(result))


def slice_contours(paths, p1, p2):
    """Slice a closed shape (given as path contour rings) with a cut line (p1 to p2).
    Returns a tuple of contour tuples for each resulting piece, or empty tuple if not sliced."""
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    length = math.hypot(dx, dy)
    if length < 0.1:
        raise ValueError("The knife cut line must have a length of at least 0.1 mm.")
    all_points = [pt for ring in paths for pt in ring]
    if not all_points:
        return ()
    xs = [pt[0] for pt in all_points]
    ys = [pt[1] for pt in all_points]
    diag = 2.0 * max(max(xs) - min(xs), max(ys) - min(ys), 10.0)
    ux, uy = dx / length, dy / length
    start = (p1[0] - diag * ux, p1[1] - diag * uy)
    end = (p2[0] + diag * ux, p2[1] + diag * uy)

    with ExitStack() as stack:
        context = _RegionContext(stack)
        rings = [_closed(points) for points in paths]
        bounds = [(min(x for x, _ in ring), min(y for _, y in ring),
                   max(x for x, _ in ring), max(y for _, y in ring)) for ring in rings]
        all_lines = list(rings) + [[start, end]]
        lines = context.read("MultiLineString", all_lines)
        noded = context.geom("GEOSUnaryUnion", lines)
        faces = context.geom("GEOSPolygonize", (c_void_p * 1)(noded), 1)
        count = context.call("GEOSGetNumGeometries", faces)
        pieces = []
        for i in range(count):
            face = context.call("GEOSGetGeometryN", faces, i)
            with ExitStack() as s:
                pt = context.geom("GEOSPointOnSurface", face, stack=s)
                x, y = c_double(), c_double()
                context.call("GEOSGeomGetX", pt, byref(x))
                context.call("GEOSGeomGetY", pt, byref(y))
            winding = sum(_winding(ring, x.value, y.value) for ring, (l, t, r, b) in zip(rings, bounds)
                          if l <= x.value <= r and t <= y.value <= b)
            if winding % 2 != 0:
                geo = context.write(face)
                if geo["type"] == "Polygon":
                    piece_rings = tuple(tuple(map(tuple, r[:-1])) for r in geo["coordinates"] if r)
                    if piece_rings:
                        pieces.append(piece_rings)
        if len(pieces) >= 2:
            return tuple(pieces)
        return ()
