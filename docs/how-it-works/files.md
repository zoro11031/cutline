# Files

## SVG import

`importer.py` reads cut paths from SVG and always reports the scale it used.
An importer that fails is harmless; one that succeeds at the wrong size cuts
the right shape at the wrong size, and nothing downstream can tell.

Rules established by testing against the SVG specification
([coordinate systems](https://www.w3.org/TR/SVG/coords.html)):

- **Bare coordinates are user units even when the page has a physical size**
  (2026-11-08). With `width`/`height` and no `viewBox`, the initial coordinate
  system is CSS pixels. Treating them as millimetres put a path ending at
  (96, 96) at 96 mm rather than 25.4 mm.
- **Rectangles keep both corner radii**, `rx` and `ry` (2026-11-10).
- **A `viewBox` honours `preserveAspectRatio`**: the default `xMidYMid meet`,
  an explicit alignment, or `none` for a deliberate stretch. `slice` clipping
  and nested viewports are refused, because their visible geometry is not
  implemented.
- **Curves are flattened in final millimetres.** Flattening before applying a
  parent scale of 20 left a 0.384 mm chord error against a 0.025 mm
  tolerance. The segment count now uses the largest stretch of the composed
  transform, including non-uniform scaling and shear.
- **A declared page must match the sheet** for registered jobs. An SVG that
  declares Letter cannot be cut with A4 marks: a disagreement over 0.1 mm in
  either axis is refused. SVGs without declared dimensions, and DXFs, carry
  no physical page, so this does not apply to them.

Checked independently: the original SVG and a polygon built from the imported
points, both rendered with `rsvg-convert` at 254 dpi, gave identical pixel
bounds, and the elliptical-corner case differed in coverage by 0.0043%,
which is the flattening.

Inkscape Silhouette's registration marks (`regmark-tl`, `-tr`, `-bl`, `-br`:
a 5 mm square, 20 mm arms, 0.3 mm stroke) are recognised by their shapes after
transforms, not by the text note the extension writes beside them.

## DXF export

Rounded cards are written as one closed polyline per card whose corner
vertices carry a bulge (group code 42) of `tan(22.5°) ≈ 0.414214` for a 90°
arc, with `$INSUNITS 4` and `$MEASUREMENT 1` so the units are not guessed.
SCM recorded that Studio draws a visible sharp corner where a `LINE` meets an
`ARC`; that is SCM's finding, taken on trust. The vertex order has to match the
sign of the bulge or the corners bulge outward.

**Studio does not import Cutline's DXF at true size** (2026-11-08). An
eight-card Letter landscape DXF opened in Studio 5.0.556.1 at 10.356 × 7.175
in for the group, where Linux reads the same file as 10.070 × 6.977 in: about
2.84% too large, with the Letter page replaced by a square mat. Import
preferences were not isolated, so this is an observation, not a diagnosed unit
conversion, and the writer is not compensated for it. It is one reason Cutline
writes `.studio3` instead.

## Studio's `.studio3` documents

Studio's free Basic edition refuses SVG ("The file type you are attempting to
access is not supported"; SVG is a Designer Edition feature). DXF opens but
carries only geometry, so page size and registration marks must be typed in
again. A `.studio3` carries everything, which is why SCM ships its templates
in that format.

Read by differential comparison of 32 SCM templates across six papers and
every card size (2026-11-15); Studio was not involved:

```
0x000..0x400   magic "silhouette05;", then a table of 64-bit fields
0x400..        document preamble: page setup, mat, registration marks
               (a second "silhouette05;" marks its start)
               card outlines
tail           layer and extension metadata, then a PNG thumbnail
```

The container is plain: not compressed, not encrypted. Strings are
length-prefixed (`uint32`, little-endian) and object types appear by name
(`Polygon`, `Default Layer`, `rounded`, `cameo5`).

- **Page size**: two `float32` millimetre values at 0x47D and 0x481, long side
  first.
- **Registration marks**: plain millimetres in the preamble. Every SCM
  template uses inset 10 mm and arm 20 mm.
- **Card outlines**: twelve `float32` pairs, four corners of three points
  each, the middle point of each arc at 45°.
- **The 64-bit field at 0x10** is a byte offset, 1041 bytes before the PNG
  thumbnail in every template sampled.
- **Not identified**: the fields at 0x20, 0x38 and 0x40. They vary with
  content but not linearly with card count (4 cards give 1708, 10 give 4098,
  21 give 8381), and two templates with the same count but different card
  sizes share a value.

Three things that mislead a reader of this format:

1. Records are not uniformly framed. The `Polygon` tag appears on some cards
   and not others, and the first card's outline comes before the first tag.
2. The spacing between consecutive points follows a pattern (`A, B, A, A, B,
   A…`) whose values differ per file, so it is recovered per outline.
3. Most templates store each outline twice, the second copy with Y negated and
   shifted by a few hundredths of a millimetre, which reverses its winding.
   Rewriting a point without keeping its outline's winding attaches each
   point's curve handles to the wrong corner.

**Rewriting is done in place.** `studio3.py` changes card coordinates without
changing the file's length, so every undecoded field still describes the same
bytes. The cost is that the card count cannot change, so a template whose count
already matches is chosen. A tested rewrite touches no byte outside the
recorded coordinate slots, and twelve points that do not close into a rounded
rectangle are rejected, not rewritten. **What Studio does when it opens a
rewritten file has not been checked.**

The reader also follows the independently written
[studio3svg format specification](https://github.com/zaydmulani09/studio3svg/blob/e8c3a58c3f9ddce0291cc04c2087c658acc748f5/SPEC.md)
for document paths, compound paths, curves and page size.

Studio 5.0.556.1 installs 39 `.studio3` samples (38 KB to 2.5 MB); every one
begins with the 13 bytes `silhouette05;` rather than a ZIP or SQLite header.

## PixScan photos

Silhouette's PixScan mats carry a pattern of black bars around a photo area,
so that a phone photo of an item on the mat can be straightened to true size.
Silhouette does not publish the geometry. It was read statically out of
Studio 5.0.556.1 on 2026-11-20 (see [What Studio does](../studying-studio/what-studio-does.md#pixscan)).

Four mats, each a set of lines of 1.5 mm bars 1.5 mm outside the photo frame,
broken by 1.5 or 3 mm gaps:

| Key | Photo area (mm) | Lines |
|---|---|---|
| 1 | 212 × 218 | 7 |
| 2 | 275 × 215 | 7 |
| 3 | 314.5 × 186 | 7 |
| 4 | 225 × 123 | 5 |

Which physical mat each key is has not been established, so they are named by
key and size.

Design's File → Import PixScan Photo (`pixscan.py`) finds the bars with a
local-contrast threshold, matches runs of gaps to the known edges in either
direction with a one-dimensional projective fit, and fits a homography to
every matched gap edge on at least two non-parallel edges. The photo is placed
at 8 px/mm as an image that is not cut. A fit worse than 0.5 mm RMS, or 1.0 mm
at any gap edge, is refused (`pixscan.check_fit`). The detection is Cutline's
own; Studio's (including its lens correction) is not reproduced.

On synthetic photos (the bars drawn, tilted, rotated and blurred), all four
mats are identified and an item lands within 0.5 mm at 0.06–0.08 mm RMS. A bar
moved 2 mm is refused; lens barrel distortion of 0.05 is refused and 0.02 is
accepted at 0.27 mm RMS. An item lying across a bar defeats detection, since
the edges left are parallel. **It has not been tried on a real photograph.**
