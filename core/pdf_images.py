"""
pdf_images.py — the image-mode input pipeline: rasterise PDF pages, downscale,
base64-encode. Split out of translation_engine.py (2026-07-29 reorg) because
both the legacy API engine and the SDK translate session feed pages through the
exact same pipeline.
"""

import base64
import io

from pdf2image import convert_from_bytes
from PIL import Image

# 200 DPI is the sweet spot for handwritten Hebrew:
# - Low enough that a 10-page scan fits in Claude's context window
# - High enough that cursive Hebrew letters are distinguishable
# At 300 DPI the image data is 2.25× larger → 2.25× more vision tokens → 2.25× cost
DPI = 200

# Anthropic's vision API downscales any image whose long edge exceeds 1568px
# (and caps requests with many images at 2000px per image). A 200-DPI A4 page is
# ~1654×2339px — its long edge is already past 1568, so the API would shrink it
# regardless, and in a many-page request it trips the 2000px hard limit outright
# (the 400 "image dimensions exceed 2000px" failures on 4.pdf/12.pdf/2020A).
# We therefore downscale to this cap OURSELVES before sending: it clears the hard
# limit and loses no detail the API wasn't going to discard anyway.
MAX_EDGE_PX = 1568


def downscale(image, max_edge: int = MAX_EDGE_PX):
    """Shrink a PIL page image so its long edge is at most max_edge, preserving
    aspect ratio. No-op if it already fits. LANCZOS is the high-quality
    downsampling filter — important for keeping cursive Hebrew legible. Returns
    the original image untouched when it's already within bounds, so pages that
    don't need shrinking pay nothing.
    """
    w, h = image.size
    longest = max(w, h)
    if longest <= max_edge:
        return image
    scale = max_edge / longest
    return image.resize((round(w * scale), round(h * scale)), Image.LANCZOS)


def pil_to_base64_png(image) -> str:
    # Claude's API requires images as base64-encoded strings, not raw bytes.
    # Steps:
    # 1. image.save(buf, "PNG") — encode the PIL image into PNG bytes, write to buf
    # 2. buf.getvalue()         — pull the raw PNG bytes out of the buffer
    # 3. base64.b64encode()     — convert bytes → base64 bytes (still bytes, not str)
    # 4. .decode("utf-8")       — convert base64 bytes → str (what JSON needs)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def render_pdf_pages_base64(pdf_bytes: bytes, dpi: int = DPI) -> list[str]:
    """Rasterise a PDF into per-page base64 PNG strings, downscaled to the cap.

    All pages are decoded into memory at once. A 10-page scan at 200 DPI can be
    ~110 MB of raw pixel data before PNG encoding. If you hit MemoryError, lower
    dpi to 150.
    """
    images = convert_from_bytes(pdf_bytes, dpi=dpi)
    print(f"  Rasterised {len(images)} page(s) at {dpi} DPI.")
    pages = []
    for i, img in enumerate(images):
        print(f"  Encoding page {i + 1}/{len(images)}...", end="\r", flush=True)
        pages.append(pil_to_base64_png(downscale(img)))
    print()  # the \r above overwrites the same terminal line; this moves past it
    return pages
