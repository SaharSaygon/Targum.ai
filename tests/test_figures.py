"""The figure pass (core/figures.py): placeholder parsing, crop math, links,
markdown rewrite, frontmatter, and the whole pass with a stub locate session.

Run: .venv/bin/python -m tests.test_figures

No sessions, no network. PDFs are built in memory with Pillow and rendered
through pdf2image (needs poppler, as the real pipeline does).
"""

import asyncio
import io
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from core import figures
from core.figures import (
    crop_box, figure_block, link_for, parse_placeholders, rewrite_markdown,
    run_figure_pass, update_frontmatter,
)

MD = """---
title: Lecture 5
course: Electromagnetic Fields
---

# Lecture 5

## Image charges

![Figure 1: point charge q at height h above a grounded plane, image charge −q below](not_included)

Some text with an inline ![Figure: tiny sketch [a,b]](not_included) in a sentence.

> ![Figure 3: not visible — text extraction only. Original referenced as "איור 3"](not_included)

![Logo of the university](not_included)

## Glossary
| Hebrew | English |
"""


def _pdf_with_boxes(boxes_per_page, size=(850, 1100)):
    """A PDF whose page i has a filled black rectangle at each pixel box."""
    pages = []
    for boxes in boxes_per_page:
        img = Image.new("RGB", size, "white")
        d = ImageDraw.Draw(img)
        for b in boxes:
            d.rectangle(b, fill="black")
        pages.append(img)
    buf = io.BytesIO()
    pages[0].save(buf, format="PDF", save_all=True, append_images=pages[1:], resolution=100)
    return buf.getvalue()


class ParseTests(unittest.TestCase):
    def test_finds_all_in_order_with_headings(self):
        ph = parse_placeholders(MD)
        self.assertEqual([p.index for p in ph], [1, 2, 3, 4])
        self.assertEqual(ph[0].heading, "Image charges")
        self.assertIn("[a,b]", ph[1].alt)          # brackets inside the alt survive
        self.assertTrue(ph[2].text_mode)
        self.assertFalse(ph[0].text_mode)

    def test_does_not_swallow_real_image_link(self):
        md = "![real](a.png) and ![Figure: x](not_included)"
        ph = parse_placeholders(md)
        self.assertEqual(len(ph), 1)
        self.assertEqual(ph[0].alt, "Figure: x")

    def test_other_targets_ignored(self):
        self.assertEqual(parse_placeholders("![x](attached) ![y](embedded)"), [])


class CropBoxTests(unittest.TestCase):
    def test_padding_and_scale(self):
        # 20 px padding = 2% of the 1000-px long edge.
        self.assertEqual(crop_box([100, 200, 300, 400], (1000, 500)), (80, 80, 320, 220))

    def test_clamped_and_swapped(self):
        self.assertEqual(crop_box([1000, 1000, 0, 0], (500, 500)), (0, 0, 500, 500))

    def test_empty_box_rejected(self):
        with self.assertRaises(ValueError):
            crop_box([500, 500, 500, 500], (100, 100), pad=0)


class LinkTests(unittest.TestCase):
    def test_hebrew_stem_with_spaces(self):
        md = Path("/v/EM/Lectures/שדות - הרצאה 5_EN.md")
        crop = figures.figures_dir_for(md) / "p07-f03.png"
        self.assertEqual(crop, Path("/v/EM/Lectures/_figures/שדות - הרצאה 5/p07-f03.png"))
        self.assertEqual(link_for(md, crop), "_figures/שדות%20-%20הרצאה%205/p07-f03.png")

    def test_parentheses_encoded(self):
        md = Path("/v/C/Exams/Moed A (2024)_EN.md")
        crop = figures.figures_dir_for(md) / "p01-f01.png"
        self.assertEqual(link_for(md, crop), "_figures/Moed%20A%20%282024%29/p01-f01.png")


class BlockTests(unittest.TestCase):
    def test_block_with_labels_and_data(self):
        lines = figure_block(7, "x.png", "Figure 3: a  plane\nwith charge",
                             [{"he": "מישור", "en": "plane"}], ["h = 2 m (approx.)"])
        self.assertEqual(lines[0], "![Figure (p.7)](x.png)")
        self.assertEqual(lines[1], "*Source p.7:* Figure 3: a plane with charge")
        self.assertIn("> [!figure]- Labels & data", lines)
        self.assertIn("> | מישור | plane |", lines)
        self.assertIn("> - h = 2 m (approx.)", lines)

    def test_block_without_extras_has_no_callout(self):
        self.assertEqual(len(figure_block(1, "x.png", "c", [], [])), 2)


class RewriteTests(unittest.TestCase):
    def _fig(self, n, page=1):
        return {"n": n, "page": page, "link": f"_figures/s/p01-f0{n}.png",
                "caption": f"cap {n}", "labels": [], "data": ["d"]}

    def test_whole_line_inline_quote_and_untouched(self):
        out = rewrite_markdown(MD, {1: self._fig(1), 2: self._fig(2), 3: self._fig(3)}, [])
        self.assertIn("![Figure (p.1)](_figures/s/p01-f01.png)\n*Source p.1:* cap 1", out)
        # Inline placeholder → inline image, details in the end section.
        self.assertIn("an inline ![Figure (p.1)](_figures/s/p01-f02.png) in a sentence", out)
        self.assertIn("### Figure f02 (p.1)\n\n*Source p.1:* cap 2", out)
        # Blockquoted placeholder keeps the quote prefix on every line.
        self.assertIn("> ![Figure (p.1)](_figures/s/p01-f03.png)\n> *Source p.1:* cap 3", out)
        # Unplaced (decorative) placeholder untouched.
        self.assertIn("![Logo of the university](not_included)", out)
        # Details section sits before the Glossary.
        self.assertLess(out.index(figures.DETAILS_HEADING), out.index("## Glossary"))

    def test_extra_figures_get_full_block(self):
        out = rewrite_markdown(MD, {}, [self._fig(5, page=2)])
        self.assertIn("### Figure f05 (p.2)\n\n![Figure (p.2)]", out)


class FrontmatterTests(unittest.TestCase):
    def test_adds_and_replaces_keeping_order(self):
        out = update_frontmatter(MD, {"figure_count": 3, "title": "New"})
        head = out.split("---")[1]
        self.assertTrue(head.strip().startswith("title: New"))
        self.assertIn("course: Electromagnetic Fields", head)
        self.assertTrue(head.strip().endswith("figure_count: 3"))
        self.assertIn("# Lecture 5", out)

    def test_hebrew_value_quoted_safely(self):
        out = update_frontmatter(MD, {"figures_dir": "_figures/שדות - הרצאה: 5"})
        self.assertIn("figures_dir: '_figures/שדות - הרצאה: 5'", out)


class PassTests(unittest.TestCase):
    """The whole pass on a 2-page PDF with a stub locate session."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.md_path = Path(self.tmp.name) / "Lectures" / "שדות - הרצאה 5_EN.md"
        self.md_path.parent.mkdir()
        self.md_path.write_text(MD, encoding="utf-8")
        # Page 1: a box in the top half. Page 2: a box near the bottom.
        self.pdf = _pdf_with_boxes([[(85, 110, 425, 330)], [(400, 800, 800, 1000)]])
        self.calls = 0

    def tearDown(self):
        self.tmp.cleanup()

    async def _locate(self, placeholders, source_bytes, source_kind):
        self.calls += 1
        if self.calls > 1:      # a re-run: the leftovers stay unplaceable
            return {"figures": [], "not_found": [1, 2]}, None
        return {"figures": [
            {"index": 1, "page": 1, "bbox": [100, 100, 500, 300], "kind": "geometry",
             "caption_en": None, "labels": [{"he": "מישור", "en": "plane"}], "data": ["h"]},
            {"index": 3, "page": 2, "bbox": [470, 727, 941, 909], "kind": "plot",
             "caption_en": "Field magnitude vs distance", "labels": [], "data": []},
            {"index": 4, "page": 1, "bbox": [0, 0, 100, 50], "kind": "decorative",
             "caption_en": None, "labels": [], "data": []},
            {"index": None, "page": 2, "bbox": [0, 0, 200, 200], "kind": "photo",
             "caption_en": "Unreferenced photo", "labels": [], "data": []},
        ], "not_found": [2]}, None

    def _run(self, **kw):
        return asyncio.run(run_figure_pass(self.md_path, self.pdf, "pdf", "text",
                                           "DRIVE123", self._locate, **kw))

    def test_full_pass(self):
        rec = self._run()
        self.assertEqual((rec["status"], rec["count"], rec["unresolved"]), ("done", 3, 2))
        fig_dir = self.md_path.parent / "_figures" / "שדות - הרצאה 5"
        names = sorted(p.name for p in fig_dir.iterdir())
        self.assertEqual(names, ["p01-f01.png", "p02-f02.png", "p02-f03.png"])
        # The page-1 crop covers the black box (crops are rendered at 300 DPI).
        crop = Image.open(fig_dir / "p01-f01.png").convert("L")
        self.assertLess(crop.getextrema()[0], 10)
        out = self.md_path.read_text(encoding="utf-8")
        self.assertIn("(_figures/שדות%20-%20הרצאה%205/p01-f01.png)", out)
        # Text-mode placeholder gets the session's caption.
        self.assertIn("*Source p.2:* Field magnitude vs distance", out)
        self.assertIn("Unreferenced photo", out)
        self.assertIn("source_url: https://drive.google.com/file/d/DRIVE123/view", out)
        self.assertIn("figure_count: 3", out)
        self.assertIn("translation_mode: text", out)
        # Not found + decorative left as placeholders.
        self.assertEqual(len(parse_placeholders(out)), 2)

    def test_rerun_does_not_duplicate(self):
        self._run()
        first = self.md_path.read_text(encoding="utf-8")
        rec = self._run()
        second = self.md_path.read_text(encoding="utf-8")
        # Leftover placeholders 2 and 4 are now indices 1 and 2: the stub
        # places nothing new for them, and unreferenced figures are first-run only.
        self.assertEqual(second.count("Unreferenced photo"), 1)
        self.assertEqual(first, second)
        self.assertEqual(rec["unresolved"], 2)

    def test_dry_run_writes_nothing(self):
        rec = self._run(dry_run=True)
        self.assertEqual(self.md_path.read_text(encoding="utf-8"), MD)
        self.assertFalse((self.md_path.parent / "_figures").exists())
        self.assertEqual(len(rec["plan"]), 3)
        self.assertIn("p01-f01.png", rec["markdown"])

    def test_no_placeholders_skips_session(self):
        self.md_path.write_text("# nothing\n", encoding="utf-8")
        rec = self._run()
        self.assertEqual(rec["status"], "none")
        self.assertEqual(self.calls, 0)

    def test_locate_failure_leaves_md_untouched(self):
        async def boom(*a):
            raise RuntimeError("session died")
        with self.assertRaises(RuntimeError):
            asyncio.run(run_figure_pass(self.md_path, self.pdf, "pdf", "text",
                                        None, boom))
        self.assertEqual(self.md_path.read_text(encoding="utf-8"), MD)


if __name__ == "__main__":
    unittest.main()
