"""Silhouette Studio `.studio3` cutting templates: read, and rewrite in place.

Why this exists
---------------
Silhouette Studio's free **Basic** edition refuses SVG outright -- "The file
type you are attempting to access is not supported" -- and SVG import is a
Designer Edition feature.  DXF does open in Basic, but a DXF carries only
geometry: the page size and the registration-mark setup still have to be typed
into Studio by hand, and section 26 of ``docs/FINDINGS.md`` records a live
Studio DXF import that arrived ~2.84% oversized with the page size not
preserved.  A ``.studio3`` carries all of it, which is why Silhouette Card
Maker ships 98 of them instead of vector files.

What is actually understood
---------------------------
The container is plain, uncompressed and unencrypted::

    0x000..0x400   header: magic ``silhouette05;`` and a table of 64-bit fields
    0x400..        document preamble: page setup, mat, registration marks
    0x???          N card records, each exactly CARD_STRIDE bytes
    tail           layer/extension metadata, then a PNG thumbnail

Every card record is the same length in every template sampled, whatever the
paper or the card size, and each holds the card outline **twice**: a main copy
in page coordinates and a mirrored copy whose Y is negated and which is offset
from the main one by a small per-file translation.  Each outline is twelve
points -- four corners of three points each, a rounded rectangle.

Only one header field has been identified: the 64-bit value at 0x10 is a byte
offset, exactly 1041 bytes before the PNG thumbnail, in all seven samples.  The
fields at 0x20, 0x38 and 0x40 vary with content in ways not yet explained; they
are **not** linear in the card count, so this module refuses to change the
number of cards in a file.

That refusal is the whole safety model.  Rewriting coordinates in place changes
no lengths, no offsets and no counts, so every unidentified field stays correct
by construction.  Growing or shrinking the card list would move the tail and
invalidate at least the 0x10 offset, and probably more.  So a template is
chosen whose card count already matches the sheet, and only numbers are
rewritten.

**Not verified against Silhouette Studio.**  No Studio on this machine.  The
round-trip and the geometry are checked here; what Studio does with the result
is not, and section 29 of ``docs/FINDINGS.md`` keeps the two apart.
"""

from __future__ import annotations

import dataclasses
import struct

MAGIC = b"silhouette05;"

#: Every card record is this long, in every template sampled.
CARD_STRIDE = 1233

#: Card records are found by this tag; it appears once per card and nowhere else.
CARD_TAG = b"Polygon"

#: Page width then height, as float32 millimetres, in the document preamble.
#: 0x47D is the long side: Card Maker lays its templates out with the long
#: edge horizontal, and the card grid confirms it -- a legal template's cards
#: run out to x = 337.8 mm, which only fits across the 355.6 mm side.
PAGE_W_OFF = 0x47D
PAGE_H_OFF = 0x481

#: An outline's twelve points are evenly framed by per-vertex handle data, so
#: they sit at a repeating gap pattern [A, B, A, A, B, A, ...]. A and B are
#: *not* constant across templates, so they are recovered from each file rather
#: than assumed, and the points are then checked to close into a rectangle.
GAP_MIN, GAP_MAX = 8, 80

#: Outlines smaller than this are not cards; it keeps the scan off stray floats.
MIN_CARD_MM = 10.0

#: Below this a float is noise or padding rather than a card coordinate.
_MIN_MM = 0.5

#: A point is only believable as part of a card outline inside this range.
_MAX_MM = 2000.0

#: Geometry read back from a template must close to this tolerance, in mm.
TOL_MM = 0.01


class Studio3Error(ValueError):
    """The file is not a `.studio3` this module is prepared to touch."""


@dataclasses.dataclass(frozen=True)
class Card:
    """One card outline: an axis-aligned rounded rectangle, in millimetres."""

    x: float
    y: float
    w: float
    h: float
    radius: float

    @property
    def points(self) -> "list[tuple[float, float]]":
        """The twelve outline points, in the order the format stores them."""
        return _outline(self.x, self.y, self.w, self.h, self.radius)


def _outline(x: float, y: float, w: float, h: float, r: float):
    """Twelve points: four corners of three, starting at the top-right corner.

    The order is the one read out of Card Maker's own templates, and it has to
    be kept: the per-vertex bezier handles that follow each point in the file
    are not rewritten, so a point must land where its handles expect it.
    """
    x1, y1 = x + w, y + h
    return [
        (x1 - r, y1), (x1 - r * 0.2928932188134524, y1 - r * 0.2928932188134524), (x1, y1 - r),
        (x1, y + r), (x1 - r * 0.2928932188134524, y + r * 0.2928932188134524), (x1 - r, y),
        (x + r, y), (x + r * 0.2928932188134524, y + r * 0.2928932188134524), (x, y + r),
        (x, y1 - r), (x + r * 0.2928932188134524, y1 - r * 0.2928932188134524), (x + r, y1),
    ]


def _f32(data: bytes, off: int) -> float:
    return struct.unpack_from("<f", data, off)[0]


def _put_f32(buf: bytearray, off: int, v: float) -> None:
    struct.pack_into("<f", buf, off, v)


def _gaps(a: int, b: int):
    """The eleven steps between an outline's twelve points."""
    return tuple(b if i % 3 == 1 else a for i in range(11))


def _offsets(start: int, gaps) -> "list[int]":
    offs, at = [start], start
    for g in gaps:
        at += g
        offs.append(at)
    return offs


def _read(data: bytes, offs) -> "list[tuple[float, float]]":
    return [struct.unpack_from("<ff", data, o) for o in offs]


def _coordish(data: bytes, off: int) -> bool:
    if off + 8 > len(data):
        return False
    x, y = struct.unpack_from("<ff", data, off)
    return (x == x and y == y
            and _MIN_MM < abs(x) < _MAX_MM and _MIN_MM < abs(y) < _MAX_MM)


def _reflect(pts, card):
    """The same outline wound the other way: reflected about its own centre."""
    span = 2 * card.y + card.h
    return [(x, span - y) for x, y in pts]


def _fit_card(pts, flip_y=False):
    """Fit twelve points to a rounded rectangle, or None if they do not.

    Returns (card, reflected). The two stored copies of an outline wind in
    opposite directions -- negating Y reverses the winding -- so both orders
    have to be accepted, and which one this was has to be remembered or a
    rewrite would renumber the points and leave each one's bezier handles,
    which are not rewritten, attached to the wrong corner.

    This is the whole safety model for the scan: a run of twelve float pairs at
    the right spacing that is *not* a card outline will not close into a
    rectangle, so it is rejected rather than rewritten.
    """
    if flip_y:
        pts = [(x, -y) for x, y in pts]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x, y = min(xs), min(ys)
    w, h = max(xs) - x, max(ys) - y
    if w < MIN_CARD_MM or h < MIN_CARD_MM:
        return None
    top = [px for px, py in pts if abs(py - (y + h)) < TOL_MM]
    if not top:
        return None
    r = (x + w) - max(top)
    if not (0 <= r <= min(w, h) / 2):
        return None
    card = Card(x, y, w, h, r)
    for reflected in (False, True):
        want = _reflect(card.points, card) if reflected else card.points
        if all(abs(g[0] - w2[0]) <= TOL_MM and abs(g[1] - w2[1]) <= TOL_MM
               for g, w2 in zip(pts, want)):
            return card, reflected
    return None


@dataclasses.dataclass(frozen=True)
class Outline:
    """One card outline found in a file: where its twelve points are, and what
    shape they make. `flipped` marks the mirrored copy, whose Y is negated."""

    offsets: tuple
    card: Card
    flipped: bool
    reflected: bool


def _scan(data: bytes) -> "list[Outline]":
    """Every card outline in the file, in file order, both copies.

    The gap pattern is recovered per outline: a candidate first point, then any
    second and third point within a vertex's reach, then the pattern those two
    steps imply, and finally the rounded-rectangle check on all twelve.
    """
    cands = [o for o in range(len(data) - 8) if _coordish(data, o)]
    cand_set = set(cands)
    found, consumed = [], -1
    for s0 in cands:
        if s0 <= consumed:
            continue
        hit = None
        for a in range(GAP_MIN, GAP_MAX + 1):
            if s0 + a not in cand_set:
                continue
            for b in range(GAP_MIN, GAP_MAX + 1):
                if s0 + a + b not in cand_set:
                    continue
                gaps = _gaps(a, b)
                offs = _offsets(s0, gaps)
                if offs[-1] + 8 > len(data):
                    continue
                pts = _read(data, offs)
                flip = pts[0][1] < 0
                fit = _fit_card(pts, flip_y=flip)
                if fit is not None:
                    hit = Outline(tuple(offs), fit[0], flip, fit[1])
                    break
            if hit:
                break
        if hit:
            found.append(hit)
            consumed = hit.offsets[-1] + 7
    return found


def check(data: bytes) -> None:
    """Validate the container enough to touch it."""
    if not data.startswith(MAGIC):
        raise Studio3Error("not a .studio3 file: missing the silhouette05 magic")


def page_size(data: bytes) -> "tuple[float, float]":
    """The page as (width, height) in millimetres."""
    return _f32(data, PAGE_W_OFF), _f32(data, PAGE_H_OFF)


#: Two outlines within this distance are the same card stored twice.
SAME_CARD_MM = 1.0


def find_cards(data: bytes):
    """Group the file's outlines into cards, in reading order.

    A card is stored once or twice depending on the template: some carry a
    second copy with Y negated and shifted by a fraction of a millimetre.
    Rather than assume which, outlines are grouped by where they actually are,
    and every outline of a card is rewritten when that card moves. Each one
    remembers its own winding, sign and sub-millimetre offset, so a rewrite
    reproduces the file's own conventions instead of imposing one.
    """
    check(data)
    outs = _scan(data)
    if not outs:
        raise Studio3Error("no card outlines found")
    groups = []       # list of [representative Card, [Outline, ...]]
    for o in outs:
        for g in groups:
            c = g[0]
            if (abs(c.x - o.card.x) < SAME_CARD_MM and abs(c.y - o.card.y) < SAME_CARD_MM
                    and abs(c.w - o.card.w) < TOL_MM and abs(c.h - o.card.h) < TOL_MM):
                g[1].append(o)
                break
        else:
            groups.append([o.card, [o]])
    sizes = {len(g[1]) for g in groups}
    if len(sizes) != 1:
        raise Studio3Error(
            f"cards are stored an inconsistent number of times ({sorted(sizes)}); "
            "the file is not laid out the way this module expects"
        )
    groups.sort(key=lambda g: (round(g[0].y, 3), round(g[0].x, 3)))
    return groups


def read_cards(data: bytes) -> "list[Card]":
    """Every card in the file, validated as a rounded rectangle.

    Ordered by position -- down the page, then across -- not by where the
    outlines happen to sit in the file, so a caller can map a sheet's grid
    onto the list without caring how the template was written.
    """
    return [g[0] for g in find_cards(data)]


def copies_per_card(data: bytes) -> int:
    """How many times each card outline is stored in this template."""
    return len(find_cards(data)[0][1])


def _stored(card: Card, o: "Outline"):
    """The twelve points as this outline stores them."""
    pts = _reflect(card.points, card) if o.reflected else card.points
    return [(x, -y) for x, y in pts] if o.flipped else pts


def write_cards(data: bytes, cards, page=None) -> bytes:
    """Rewrite the card outlines, and optionally the page size, in place.

    `cards` must have exactly as many entries as the file already holds: see
    the module docstring for why the card count cannot change. Nothing else in
    the file is touched, so its length, offsets and counters stay valid.
    """
    groups = find_cards(data)
    cards = list(cards)
    if len(cards) != len(groups):
        raise Studio3Error(
            f"this template holds {len(groups)} cards and the card count cannot be "
            f"changed; got {len(cards)}. Pick a template whose count matches the sheet."
        )
    buf = bytearray(data)
    if page is not None:
        w, h = page
        _put_f32(buf, PAGE_W_OFF, float(w))
        _put_f32(buf, PAGE_H_OFF, float(h))
    for (rep, outs), new in zip(groups, cards):
        for o in outs:
            # Each copy keeps its own sub-millimetre offset from the card.
            moved = Card(new.x + (o.card.x - rep.x), new.y + (o.card.y - rep.y),
                         new.w, new.h, new.radius)
            _put_points(buf, o.offsets, _stored(moved, o))
    return bytes(buf)


def _put_points(buf: bytearray, offsets, pts) -> None:
    for off, (x, y) in zip(offsets, pts):
        _put_f32(buf, off, x)
        _put_f32(buf, off + 4, y)
