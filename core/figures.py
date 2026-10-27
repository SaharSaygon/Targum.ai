"""
figures.py — the figure pass: turn `![alt](not_included)` placeholders in a
translated .md into real cropped images stored next to it.

Runs after translation (new files, sdk/agent_sdk.py) and from the backfill
(scripts/backfill_figures.py) through the same run_figure_pass. No LLM call
lives here: the model's part (which page and where each figure sits, label
translations, data read off the figure) is the injected `locate` coroutine —
sdk/sessions.py locate_figures in production, a stub in tests.

Vault layout: <Course>/<Type>/_figures/<stem>/pPP-fNN.png, where <stem> is the
.md name without _EN. One _figures/ folder per type subfolder keeps folder
listings clean (it sorts first).

The pass only ever consumes `](not_included)` placeholders, so it is idempotent:
a second run finds nothing to do (or only the placeholders a first run could
not place), and figure numbering continues after the crops already on disk.
A failure anywhere leaves the .md untouched — the rewrite is a single atomic
replace at the very end.
"""

import io
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from PIL import Image, ImageOps

# Bumped when the block format or locate contract changes; recorded in the
# manifest so a later backfill can tell which files an older pass produced.
PASS_VERSION = 1

FIGURES_DIRNAME = "_figures"

# Crops come from a fresh render, sharper than the 200-DPI pages the model saw.
CROP_DPI = 300

# Padding around the model's box, as a fraction of the page's long edge —
# model boxes are close but not pixel-exact; a little margin never hurts.
PAD_FRACTION = 0.02

# PNG keeps line art crisp; a photo or noisy scan crop that comes out larger
# than this as PNG is re-saved as JPEG (same split as render_image_base64).
PNG_MAX_BYTES = 400_000

# Box coordinates the locate session returns: [x0, y0, x1, y1] in 0..BBOX_SCALE
# of the page width/height — independent of any render resolution.
BBOX_SCALE = 1000

# `![alt](not_included)`. The alt may itself contain brackets ("[a,b]"), so it
# is matched lazily up to the first `](not_included)`, but it may not contain
# `](` — that would mean the match started at an earlier, real image link.
PLACEHOLDER_RE = re.compile(r"!\[(?P<alt>(?:(?!\]\().)*?)\]\(not_included\)")

# A line that is nothing but a placeholder, optionally inside a blockquote
# and/or a list item: gets the full figure block. Anything else (a table cell,
# mid-sentence) gets an inline image, and its details go to the end section.
_WHOLE_LINE_RE = re.compile(
    r"^(?P<prefix>[ \t]*(?:>[ \t]?)*(?:[ \t]*(?:[-*+]|\d+\.)[ \t]+)?)"
    r"!\[(?:(?!\]\().)*?\]\(not_included\)[ \t]*$"
)

_HEADING_RE = re.compile(r"^#{1,6}\s+(.*\S)\s*$", re.M)
_TEXT_MODE_MARK = "not visible"
_EXISTING_CROP_RE = re.compile(r"^p\d+-f(\d+)\.(?:png|jpg)$")

DETAILS_HEADING = "## Figure details"


@dataclass
class Placeholder:
    index: int          # 1-based, document order — what the locate session refers to
    alt: str
    heading: str        # nearest preceding markdown heading ("" if none)
    start: int          # span of the placeholder in the markdown
    end: int

    @property
    def text_mode(self) -> bool:
        """Text-mode placeholders were written blind ("not visible — text
        extraction only"); their alt text is not a description."""
        return _TEXT_MODE_MARK in self.alt.lower()


def parse_placeholders(md: str) -> list[Placeholder]:
    """Every `![alt](not_included)` in document order, with its nearest
    preceding heading (context for the locate session)."""
    headings = [(m.start(), m.group(1)) for m in _HEADING_RE.finditer(md)]
    out = []
    for i, m in enumerate(PLACEHOLDER_RE.finditer(md), start=1):
        heading = ""
        for pos, text in headings:
            if pos > m.start():
                break
            heading = text
        out.append(Placeholder(i, m.group("alt"), heading, m.start(), m.end()))
    return out


# ── Paths and links ───────────────────────────────────────────────────────────

def md_stem(md_path: Path) -> str:
    stem = md_path.stem
    return stem[:-3] if stem.endswith("_EN") else stem


def figures_dir_for(md_path: Path) -> Path:
    return md_path.parent / FIGURES_DIRNAME / md_stem(md_path)


def crop_basename(page: int, n: int) -> str:
    """pPP-fNN, no extension (save_crop picks .png or .jpg)."""
    return f"p{page:02d}-f{n:02d}"


def link_for(md_path: Path, crop_path: Path) -> str:
    """Relative markdown link target from the .md to a crop. Hebrew stays
    readable; only the characters that break a markdown link destination are
    percent-encoded (Obsidian decodes them)."""
    rel = os.path.relpath(crop_path, md_path.parent).replace(os.sep, "/")
    for ch, enc in (("%", "%25"), (" ", "%20"), ("(", "%28"), (")", "%29"),
                    ("<", "%3C"), (">", "%3E")):
        rel = rel.replace(ch, enc)
    return rel


def drive_url(drive_file_id: str) -> str:
    return f"https://drive.google.com/file/d/{drive_file_id}/view"


def _next_figure_number(fig_dir: Path) -> int:
    """Numbering continues after the crops already on disk, so a re-run that
    places leftover placeholders never overwrites an earlier crop."""
    nums = [int(m.group(1)) for p in (fig_dir.iterdir() if fig_dir.is_dir() else [])
            if (m := _EXISTING_CROP_RE.match(p.name))]
    return max(nums, default=0) + 1


# ── Cropping ──────────────────────────────────────────────────────────────────

def crop_box(bbox, size, pad=PAD_FRACTION) -> tuple[int, int, int, int]:
    """Normalized [x0, y0, x1, y1] (0..BBOX_SCALE) → padded pixel box clamped
    to the image. Tolerates swapped corners. Raises ValueError on a box with
    no area (the caller treats that figure as unplaceable)."""
    w, h = size
    x0, y0, x1, y1 = (float(v) for v in bbox)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    padding = pad * max(w, h)
    left = max(0, int(x0 / BBOX_SCALE * w - padding))
    top = max(0, int(y0 / BBOX_SCALE * h - padding))
    right = min(w, int(round(x1 / BBOX_SCALE * w + padding)))
    bottom = min(h, int(round(y1 / BBOX_SCALE * h + padding)))
    if right - left < 2 or bottom - top < 2:
        raise ValueError(f"empty crop box {bbox}")
    return left, top, right, bottom


def render_pages(source_bytes: bytes, source_kind: str, pages: set[int],
                 dpi: int = CROP_DPI) -> dict[int, Image.Image]:
    """Full-resolution page images for the pages that hold figures (1-based).
    A PDF renders only those pages; an image source is its own page 1."""
    if source_kind == "image":
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(source_bytes)))
        return {1: img.convert("RGB") if img.mode not in ("RGB", "L") else img}
    from pdf2image import convert_from_bytes
    out = {}
    for p in sorted(pages):
        imgs = convert_from_bytes(source_bytes, dpi=dpi, first_page=p, last_page=p)
        if imgs:
            out[p] = imgs[0]
    return out


def save_crop(img: Image.Image, base: Path) -> Path:
    """Save as PNG; fall back to JPEG when PNG is too big (photos, scans).
    `base` has no extension; returns the written path."""
    base.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    if buf.tell() <= PNG_MAX_BYTES:
        path = base.with_suffix(".png")
        path.write_bytes(buf.getvalue())
        return path
    path = base.with_suffix(".jpg")
    img.convert("RGB").save(path, format="JPEG", quality=85)
    return path


# ── Markdown ──────────────────────────────────────────────────────────────────

def _one_line(text: str) -> str:
    return " ".join((text or "").split())


def figure_block(page: int, link: str, caption: str,
                 labels: list[dict], data: list[str]) -> list[str]:
    """The block that replaces a whole-line placeholder: the image, a caption
    naming the source page, and a collapsed callout with the Hebrew→English
    label key and the data read off the figure (searchable text for Claude and
    the exam pipeline). The caption keeps the source's own figure numbering
    ("Figure 7: …" from the alt text) — the crop number is only a file name."""
    lines = [f"![Figure (p.{page})]({link})",
             f"*Source p.{page}:* {_one_line(caption)}".rstrip()]
    labels = [l for l in labels or [] if l.get("he") or l.get("en")]
    data = [_one_line(d) for d in data or [] if _one_line(d)]
    if labels or data:
        lines.append("")
        lines.append("> [!figure]- Labels & data")
        if labels:
            lines += ["> | Hebrew | English |", "> |---|---|"]
            for l in labels:
                he = _one_line(l.get("he", "")).replace("|", "\\|")
                en = _one_line(l.get("en", "")).replace("|", "\\|")
                lines.append(f"> | {he} | {en} |")
        for d in data:
            lines.append(f"> - {d}")
    return lines


def _prefix_lines(lines: list[str], prefix: str) -> list[str]:
    """Indent a block under a blockquote/list prefix: quote markers are kept,
    a list marker becomes plain indentation of the same width."""
    m = re.match(r"^([ \t]*(?:>[ \t]?)*)(.*)$", prefix)
    quote, rest = m.group(1), m.group(2)
    first = quote + rest
    cont = quote + " " * len(rest)
    out = []
    for i, line in enumerate(lines):
        p = first if i == 0 else cont
        out.append((p + line).rstrip() if line else p.rstrip())
    return out


def _insert_details(md: str, sections: list[str]) -> str:
    """Append the details section before the closing Glossary (or at the end)."""
    if not sections:
        return md
    body = DETAILS_HEADING + "\n\n" + "\n\n".join(sections) + "\n"
    m = re.search(r"^## Glossary\b", md, re.M)
    if m:
        return md[:m.start()] + body + "\n" + md[m.start():]
    return md.rstrip("\n") + "\n\n" + body


def rewrite_markdown(md: str, placed: dict[int, dict], extra: list[dict]) -> str:
    """Replace placed placeholders and append unreferenced figures.

    placed: placeholder index → {"n", "page", "link", "caption", "labels", "data"}.
    extra:  the same dicts for figures no placeholder covers.
    Placeholders not in `placed` (decorative, not found) stay untouched.
    """
    placeholders = {p.index: p for p in parse_placeholders(md)}
    details = []
    # Work back to front so earlier spans stay valid.
    for idx in sorted(placed, reverse=True):
        ph, fig = placeholders[idx], placed[idx]
        line_start = md.rfind("\n", 0, ph.start) + 1
        line_end = md.find("\n", ph.end)
        line_end = len(md) if line_end == -1 else line_end
        m = _WHOLE_LINE_RE.match(md[line_start:line_end])
        if m:
            block = figure_block(fig["page"], fig["link"], fig["caption"],
                                 fig["labels"], fig["data"])
            md = md[:line_start] + "\n".join(_prefix_lines(block, m.group("prefix"))) + md[line_end:]
        else:
            md = md[:ph.start] + f"![Figure (p.{fig['page']})]({fig['link']})" + md[ph.end:]
            details.append(fig)
    sections = []
    entries = ([(f, True) for f in sorted(details, key=lambda f: f["n"])]
               + [(f, False) for f in extra])
    for fig, inline in entries:
        block = figure_block(fig["page"], fig["link"], fig["caption"],
                             fig["labels"], fig["data"])
        # An inline figure's image is already in the text; its section carries
        # only the caption and the label/data callout.
        sections.append(f"### Figure f{fig['n']:02d} (p.{fig['page']})\n\n" + "\n".join(block[1:] if inline else block))
    return _insert_details(md, sections)


def update_frontmatter(md: str, fields: dict) -> str:
    """Set top-level frontmatter keys in place (existing keys and order kept;
    new keys appended before the closing ---). No frontmatter → one is added."""
    rendered = {k: yaml.safe_dump({k: v}, allow_unicode=True, default_flow_style=False,
                                  width=10_000).strip()
                for k, v in fields.items()}
    if not md.startswith("---\n"):
        return "---\n" + "\n".join(rendered.values()) + "\n---\n\n" + md
    end = md.find("\n---", 4)
    if end == -1:
        return md
    lines = md[4:end].split("\n")
    for key, line in rendered.items():
        for i, existing in enumerate(lines):
            if existing.startswith(f"{key}:"):
                lines[i] = line
                break
        else:
            lines.append(line)
    return "---\n" + "\n".join(lines) + md[end:]


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


# ── The pass ──────────────────────────────────────────────────────────────────

def status_record(status: str, **extra) -> dict:
    return {"status": status, "pass_version": PASS_VERSION,
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **extra}


def failed_record(error: Exception) -> dict:
    """Manifest record for a pass that raised (the .md was left untouched)."""
    return status_record("failed", count=0, error=str(error)[:300])


async def run_figure_pass(md_path: Path, source_bytes: bytes, source_kind: str,
                          mode: str, drive_file_id: str | None, locate,
                          dry_run: bool = False) -> dict:
    """Locate, crop and embed every figure placeholder in md_path.

    locate: async (placeholders, source_bytes, source_kind) → (located, result)
      where located = {"figures": [...], "not_found": [...]} per the
      sdk/sessions.py LOCATE_FIGURES_SCHEMA, and result is the session's
      ResultMessage (or None) for cost accounting by the caller.
    Returns the manifest `figures` record, plus "result" (the session result)
    and, on dry_run, "plan" (the crops it would write) and "markdown".
    """
    md = md_path.read_text(encoding="utf-8")
    placeholders = parse_placeholders(md)
    if not placeholders or source_kind == "text":
        return status_record("none", count=0, unresolved=0)

    fig_dir = figures_dir_for(md_path)
    first_run = not any(fig_dir.glob("p*-f*.*")) if fig_dir.is_dir() else True
    located, result = await locate(placeholders, source_bytes, source_kind)

    by_index = {p.index: p for p in placeholders}
    referenced, extra = [], []
    for fig in located.get("figures", []):
        if fig.get("kind") == "decorative":
            continue
        idx = fig.get("index")
        if idx in by_index:
            referenced.append(fig)
        elif idx is None and first_run:
            # Unreferenced figures are added on the first pass only, so a
            # re-run for leftover placeholders never duplicates them.
            extra.append(fig)
    # A placeholder claimed twice: keep the first claim.
    seen, unique = set(), []
    for fig in referenced:
        if fig["index"] not in seen:
            seen.add(fig["index"])
            unique.append(fig)
    referenced = sorted(unique, key=lambda f: f["index"])

    figs = referenced + sorted(extra, key=lambda f: (f.get("page", 0), f["bbox"][1]))
    pages = {int(f["page"]) for f in figs}
    rendered = render_pages(source_bytes, source_kind, pages) if figs and not dry_run else {}

    n = _next_figure_number(fig_dir)
    placed, extra_out, plan = {}, [], []
    for fig in figs:
        page = int(fig["page"])
        base = fig_dir / crop_basename(page, n)
        if dry_run:
            crop_path = base.with_suffix(".png")
        else:
            img = rendered.get(page)
            if img is None:
                continue
            try:
                box = crop_box(fig["bbox"], img.size)
            except ValueError:
                continue
            crop_path = save_crop(img.crop(box), base)
        plan.append((fig.get("index"), page, fig["bbox"], crop_path.name))
        idx = fig.get("index")
        if idx is not None:
            ph = by_index[idx]
            # Vision placeholders already carry a description; a text-mode
            # one was written blind, so the locate session's caption replaces it.
            caption = (fig.get("caption_en") or ph.alt) if ph.text_mode else ph.alt
        else:
            caption = fig.get("caption_en") or ""
        entry = {"n": n, "page": page, "link": link_for(md_path, crop_path),
                 "caption": caption, "labels": fig.get("labels") or [],
                 "data": fig.get("data") or []}
        if idx is not None:
            placed[idx] = entry
        else:
            extra_out.append(entry)
        n += 1

    new_md = rewrite_markdown(md, placed, extra_out)
    unresolved = len(placeholders) - len(placed)
    total = len([p for p in fig_dir.glob("p*-f*.*")]) if fig_dir.is_dir() else 0
    fm = {"translation_mode": mode, "figures_dir": f"{FIGURES_DIRNAME}/{md_stem(md_path)}",
          "figure_count": total if not dry_run else len(plan)}
    if drive_file_id:
        fm = {"drive_file_id": drive_file_id, "source_url": drive_url(drive_file_id), **fm}
    new_md = update_frontmatter(new_md, fm)

    record = status_record("done", count=len(plan), unresolved=unresolved)
    record["result"] = result
    if dry_run:
        record["plan"] = plan
        record["markdown"] = new_md
    else:
        atomic_write(md_path, new_md)
    return record
