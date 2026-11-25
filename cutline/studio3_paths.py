"""Read page-space vectors in the silhouette05 document section.

Binary layout reference: zaydmulani09/studio3svg, SPEC.md sections 2–5.
The rounded-card template writer lives separately in studio3.py.
"""

from __future__ import annotations

import dataclasses
import math
import struct

from .studio3 import MAGIC, Studio3Error


@dataclasses.dataclass(frozen=True)
class Vertex:
    point: tuple[float, float]
    incoming: tuple[float, float] | None
    outgoing: tuple[float, float] | None


@dataclasses.dataclass(frozen=True)
class Outline:
    name: str
    closed: bool
    vertices: tuple[Vertex, ...]


def _integer(data, offset, signed=False):
    return struct.unpack_from("<i" if signed else "<I", data, offset)[0]


def _block(data, offset, limit):
    if offset + 5 > limit or data[offset] != 1:
        return None
    end = offset + 1 + _integer(data, offset + 1)
    if offset + 5 <= end <= limit:
        return offset + 5, end
    return None


def _require_block(data, offset, limit):
    block = _block(data, offset, limit)
    if block is None:
        raise Studio3Error("Invalid Studio3 document block.")
    return block


def _vector(data, offset, limit):
    if offset + 8 > limit:
        raise Studio3Error("Truncated Studio3 vector.")
    pair = struct.unpack_from("<ff", data, offset)
    if not all(math.isfinite(coord) for coord in pair):
        raise Studio3Error("Invalid Studio3 vector coordinate.")
    return pair


def _handle(data, offset, limit):
    if offset >= limit or data[offset] not in (0, 1):
        raise Studio3Error("Invalid Studio3 Bézier handle.")
    if data[offset] == 0:
        return None, offset + 1
    return _vector(data, offset + 1, limit), offset + 9


def _subpaths(data, offset, end, name):
    if end - offset < 4:
        raise Studio3Error("Truncated Studio3 outline.")
    subpaths = _integer(data, offset, signed=True) + 1
    offset += 4
    if not 0 <= subpaths <= (end - offset) // 9:
        raise Studio3Error("Invalid Studio3 subpath count.")
    for _ in range(subpaths):
        if end - offset < 9:
            raise Studio3Error("Truncated Studio3 subpath.")
        stop = offset + _integer(data, offset)
        closed = data[offset + 4]
        count = _integer(data, offset + 5, signed=True) + 1
        offset += 9
        if (closed not in (0, 1) or stop > end
                or stop < offset or not 0 <= count <= (stop - offset) // 10):
            raise Studio3Error("Invalid Studio3 subpath.")
        vertices = []
        for _ in range(count):
            point = _vector(data, offset, stop)
            incoming, offset = _handle(data, offset + 8, stop)
            outgoing, offset = _handle(data, offset, stop)
            vertices.append(Vertex(point, incoming, outgoing))
        if offset != stop:
            raise Studio3Error("Studio3 subpath size does not match its points.")
        if len(vertices) >= 2:
            yield Outline(name, bool(closed), tuple(vertices))
    if offset != end:
        raise Studio3Error("Studio3 outline size does not match its subpaths.")


def _shape_outline(data, offset, root_end):
    if data[offset - 1] != 0:
        return None
    properties = _block(data, offset, root_end)
    if properties is None:
        return None
    # A 65-byte transform separates the shape properties from page-space points.
    outline = _block(data, properties[1] + 65, root_end)
    if outline is None:
        return None
    _, end = outline
    if end + 9 >= root_end or data[end] != 1:
        return None
    tail = _block(data, end + 9, root_end)
    if tail is None:
        return None
    name_offset = tail[1]
    if name_offset + 4 > root_end:
        return None
    size = _integer(data, name_offset)
    start = name_offset + 4
    if start + size > root_end:
        return None
    try:
        name = data[start:start + size].decode("utf-8")
    except UnicodeDecodeError:
        return None
    if name and not name.isprintable():
        return None
    return outline, name


def load_vectors(data):
    """Return the declared page size and vector outlines in document order."""
    if len(data) < 0x400 or not data.startswith(MAGIC):
        raise Studio3Error("Expected a .studio3 document with the silhouette05 header.")
    document_end = 0x400 + struct.unpack_from("<Q", data, 0x10)[0]
    if document_end > len(data) or data[0x400:0x40D] != MAGIC:
        raise Studio3Error("Invalid or truncated Studio3 document section.")
    first, root_end = _require_block(data, 0x40D, document_end)
    _, first_end = _require_block(data, first, root_end)
    page_block, page_end = _require_block(data, first_end, root_end)
    page = _vector(data, page_block + 0x29, page_end)
    if min(page) <= 0 or max(page) > 100_000:
        raise Studio3Error("Invalid Studio3 page dimensions.")

    # Child blocks can be inside properties or between raw values. Scan block
    # markers and deduplicate by the page-space block, which has one record tail.
    outlines = {}
    marker = data.find(b"\x01", page_end, root_end)
    while marker != -1:
        shape = _shape_outline(data, marker, root_end)
        if shape is not None:
            (start, end), name = shape
            if start not in outlines:
                outlines[start] = tuple(_subpaths(data, start, end, name))
        marker = data.find(b"\x01", marker + 1, root_end)
    paths = tuple(path for start in sorted(outlines) for path in outlines[start])
    if not paths:
        raise Studio3Error("No supported vector outlines found in the Studio3 document.")
    return page, paths


def encode_path(outlines):
    """Encode a Studio3 page-space path block from its ordered subpaths."""
    def vector(pair):
        try:
            x, y = pair
            if math.isfinite(x) and math.isfinite(y):
                return struct.pack("<ff", x, y)
        except (TypeError, ValueError, OverflowError, struct.error):
            pass
        raise Studio3Error("Invalid or unrepresentable Studio3 vector coordinate.")

    def handle(pair):
        return b"\x00" if pair is None else b"\x01" + vector(pair)

    subpaths = []
    for outline in outlines:
        if type(outline.closed) is not bool or len(outline.vertices) < 2:
            raise Studio3Error("A Studio3 subpath needs an open or closed flag and at least two vertices.")
        vertices = b"".join(vector(vertex.point) + handle(vertex.incoming) + handle(vertex.outgoing)
                            for vertex in outline.vertices)
        subpaths.append(struct.pack("<IBi", 9 + len(vertices), outline.closed, len(outline.vertices) - 1)
                        + vertices)
    body = struct.pack("<i", len(subpaths) - 1) + b"".join(subpaths)
    return b"\x01" + struct.pack("<I", len(body) + 4) + body
