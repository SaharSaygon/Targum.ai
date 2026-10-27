"""Non-PDF sources (photographed pages, CSV/text files) on the SDK path.

Run: .venv/bin/python -m tests.test_non_pdf_sources

No sessions, no network: covers byte sniffing, single-page signals, the image
encoder, and the route prompt's source_format / preview lines.
"""

import base64
import io
import unittest

from PIL import Image

from core.pdf_images import MAX_EDGE_PX, render_image_base64
from sdk.prepare import _single_page_signals, sniff_source_kind
from tests.test_sdk_port import _ctx
from sdk.sessions import _route_prompt


def _jpeg(size=(3000, 4000), exif_orientation=None):
    buf = io.BytesIO()
    img = Image.new("RGB", size, "white")
    exif = Image.Exif()
    if exif_orientation:
        exif[0x0112] = exif_orientation
    img.save(buf, format="JPEG", exif=exif)
    return buf.getvalue()


class SniffTests(unittest.TestCase):
    def test_pdf(self):
        self.assertEqual(sniff_source_kind(b"%PDF-1.7\n..."), "pdf")

    def test_jpeg(self):
        self.assertEqual(sniff_source_kind(_jpeg((10, 10))), "image")

    def test_csv_with_bom(self):
        data = "﻿old,new\nהרצאה 1.jpg,lec1.jpg\n".encode("utf-8")
        self.assertEqual(sniff_source_kind(data), "text")

    def test_unknown_binary_falls_to_pdf(self):
        self.assertEqual(sniff_source_kind(b"\x00\xff\xfe\x00garbage"), "pdf")


class SignalsTests(unittest.TestCase):
    def test_image_is_zero_yield_one_page(self):
        s = _single_page_signals(400_000, "")
        self.assertEqual(s["page_count"], 1)
        self.assertEqual(s["tokens_per_page"], 0.0)
        self.assertEqual(s["recognizability"], 0.0)

    def test_text_has_yield(self):
        s = _single_page_signals(100, "שלום עולם hello world")
        self.assertEqual(s["tokens_per_page"], 4.0)
        self.assertEqual(s["recognizability"], 1.0)


class ImageEncodeTests(unittest.TestCase):
    def test_downscaled_jpeg(self):
        [(mt, b64)] = render_image_base64(_jpeg())
        self.assertEqual(mt, "image/jpeg")
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        self.assertEqual(max(img.size), MAX_EDGE_PX)

    def test_exif_rotation_applied(self):
        # Orientation 6 = stored landscape, displayed portrait.
        [(_, b64)] = render_image_base64(_jpeg((400, 300), exif_orientation=6))
        img = Image.open(io.BytesIO(base64.b64decode(b64)))
        self.assertEqual(img.size, (300, 400))


class RoutePromptFormatTests(unittest.TestCase):
    def test_pdf_line(self):
        self.assertIn("source_format: pdf", _route_prompt(_ctx(), "", None))

    def test_image_line(self):
        p = _route_prompt(_ctx(source_kind="image"), "", None)
        self.assertIn("only mode=image is possible", p)
        self.assertNotIn("Content preview", p)

    def test_text_preview(self):
        p = _route_prompt(_ctx(source_kind="text", source_text="old,new\nא,ב"), "", None)
        self.assertIn("only mode=text is possible", p)
        self.assertIn("## Content preview", p)
        self.assertIn("old,new\nא,ב", p)


if __name__ == "__main__":
    unittest.main()
