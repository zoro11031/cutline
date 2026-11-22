"""Portable raster artwork for Design projects."""

import base64
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO

FORMATS = ("PNG", "JPEG", "WEBP")
MAX_PIXELS = 24_000_000
MAX_EDGE = 3200
MAX_ENCODED_BYTES = 56_000_000
SOURCES = ("Automatic", "Transparency", "Dark artwork", "Light artwork")


def pillow():
    try:
        from PIL import Image
    except ImportError as error:
        raise ValueError("Install Pillow to use sticker artwork.") from error
    return Image


@dataclass(frozen=True)
class Sticker:
    png: str
    name: str
    bleed_mm: float = 0.0
    trace_source: str = "Automatic"
    threshold: int = 128
    keep_holes: bool = True

    def validate(self):
        if (not isinstance(self.name, str) or not self.name or len(self.name) > 255
                or type(self.bleed_mm) not in (int, float) or not 0 <= self.bleed_mm <= 5
                or self.trace_source not in SOURCES or type(self.threshold) is not int
                or not 1 <= self.threshold <= 254 or type(self.keep_holes) is not bool):
            raise ValueError("Invalid sticker settings.")
        self.image()

    def image(self):
        return _image(self.png)

    def png_bytes(self):
        return _png_bytes(self.png)


@lru_cache(maxsize=4)
def _png_bytes(encoded):
    if not isinstance(encoded, str) or len(encoded) > MAX_ENCODED_BYTES:
        raise ValueError("The embedded sticker image is too large.")
    return base64.b64decode(encoded, validate=True)


@lru_cache(maxsize=4)
def _image(encoded):
    """Share decoded, read-only artwork across previews, traces and export."""
    Image = pillow()
    try:
        with Image.open(BytesIO(_png_bytes(encoded)), formats=("PNG",)) as image:
            if max(image.size) > MAX_EDGE or getattr(image, "n_frames", 1) != 1:
                raise ValueError("The project contains an oversized or animated sticker.")
            return image.convert("RGBA")
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError("Could not read the sticker image embedded in the project.") from error


def load(path):
    Image = pillow()
    from PIL import ImageOps

    try:
        with Image.open(path, formats=FORMATS) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("Choose a sticker image under 24 megapixels.")
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("Choose a still PNG, JPEG, or WebP image.")
            dpi = source.info.get("dpi", (300, 300))
            ppi = dpi[0] if isinstance(dpi, tuple) and dpi and 72 <= dpi[0] <= 2400 else 300
            image = ImageOps.exif_transpose(source).convert("RGBA")
            size_mm = tuple(edge * 25.4 / ppi for edge in image.size)
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            data = BytesIO()
            image.save(data, "PNG")
    except (OSError, Image.DecompressionBombError) as error:
        raise ValueError(f"Could not open {path.name} as a still PNG, JPEG, or WebP image.") from error
    return Sticker(base64.b64encode(data.getvalue()).decode("ascii"), path.name), size_mm
