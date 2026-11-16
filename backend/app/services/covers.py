"""Album-art storage: dedupe by content hash, normalise to JPEG, build a
thumbnail, and extract a dominant colour + palette for the ambient theme."""
from __future__ import annotations

import colorsys
import hashlib
import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import get_settings

FULL_MAX = 1200
THUMB_SIZE = 300


@dataclass
class CoverInfo:
    cover_path: str          # relative to COVER_CACHE_DIR, e.g. "ab/ab12...jpg"
    cover_hash: str
    dominant_color: str      # "#rrggbb"
    palette: list[str]


def cover_root() -> Path:
    return get_settings().cover_cache_dir


def thumb_rel(cover_path: str, size: int = THUMB_SIZE) -> str:
    p = Path(cover_path)
    return str(p.with_name(f"{p.stem}_{size}{p.suffix}")).replace("\\", "/")


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def extract_palette(img: Image.Image, count: int = 5) -> tuple[str, list[str]]:
    """Return (dominant, palette). Dominant favours colourful, mid-bright hues
    over the large black/white areas common on covers, which make dull gradients."""
    small = img.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR)
    q = small.quantize(colors=10, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette() or []
    counts = sorted(q.getcolors() or [], reverse=True)  # [(count, index)]

    colours: list[tuple[int, tuple[int, int, int]]] = []
    for cnt, idx in counts:
        rgb = tuple(pal[idx * 3: idx * 3 + 3])
        if len(rgb) == 3:
            colours.append((cnt, rgb))  # type: ignore[arg-type]
    if not colours:
        return "#333333", ["#333333"]

    total = sum(c for c, _ in colours)

    def score(item: tuple[int, tuple[int, int, int]]) -> float:
        cnt, (r, g, b) = item
        _, light, sat = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        # penalise near-black / near-white, reward saturation, keep popularity
        light_w = 1.0 - abs(light - 0.5) * 1.6
        return (cnt / total) * (0.35 + sat) * max(light_w, 0.05)

    dominant = max(colours, key=score)[1]
    palette = [_hex(rgb) for _, rgb in colours[:count]]
    return _hex(dominant), palette


def store_cover(data: bytes, *, crop_square: bool = False) -> CoverInfo | None:
    """Persist image bytes (idempotent). Returns None if the bytes aren't an image.

    crop_square: centre-crop wide images (16:9 video thumbnails) to 1:1 album art.
    """
    if not data:
        return None
    digest = hashlib.sha1(data + (b"#sq" if crop_square else b"")).hexdigest()
    rel = f"{digest[:2]}/{digest}.jpg"
    full = cover_root() / rel
    thumb = cover_root() / thumb_rel(rel)

    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.load()
    except (UnidentifiedImageError, OSError, ValueError):
        return None
    if img.mode not in ("RGB", "L"):
        bg = Image.new("RGB", img.size, (0, 0, 0))
        rgba = img.convert("RGBA")
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    img = img.convert("RGB")

    if crop_square and img.width > img.height * 1.15:
        side = img.height
        left = (img.width - side) // 2
        img = img.crop((left, 0, left + side, side))

    if not full.exists() or not thumb.exists():
        full.parent.mkdir(parents=True, exist_ok=True)
        big = img.copy()
        big.thumbnail((FULL_MAX, FULL_MAX), Image.Resampling.LANCZOS)
        big.save(full, "JPEG", quality=88, optimize=True, progressive=True)
        small = img.copy()
        small.thumbnail((THUMB_SIZE, THUMB_SIZE), Image.Resampling.LANCZOS)
        small.save(thumb, "JPEG", quality=82, optimize=True, progressive=True)

    dominant, palette = extract_palette(img)
    return CoverInfo(rel, digest, dominant, palette)
