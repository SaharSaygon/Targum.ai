"""
translation_engine.py — core translation logic shared by all entry points.

Both public functions have the same signature and return the same dict shape:
    {markdown, input_tokens, output_tokens, cost_usd, model, mode}

today_date (YYYY-MM-DD) is injected into every user message so Claude can
write an accurate date_translated frontmatter field without fabricating it.
"""

import base64
import hashlib
import io
import os
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import pypdf
from pdf2image import convert_from_bytes

import manifest

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL = "claude-opus-4-8"

# 200 DPI is the sweet spot for handwritten Hebrew:
# - Low enough that a 10-page scan fits in Claude's context window
# - High enough that cursive Hebrew letters are distinguishable
# At 300 DPI the image data is 2.25× larger → 2.25× more vision tokens → 2.25× cost
DPI = 200

# Path(__file__) is the absolute path of *this file* on disk.
# .parent strips the filename, leaving just the directory it lives in.
# This means "find the prompt files next to me", regardless of which
# directory the user runs the script from.
_PROJECT_ROOT = Path(__file__).parent

def _load_skill(name: str) -> str:
    """Read a skill file from skills/ by name (UTF-8, _PROJECT_ROOT-relative).

    Each translation tool's system prompt is translate-shared.md concatenated
    with that tool's own mode skill — see _TEXT_SYSTEM_PROMPT / _IMAGE_SYSTEM_PROMPT.
    """
    return (_PROJECT_ROOT / "skills" / name).read_text(encoding="utf-8")


# Load the skill files once when the module is first imported, not on every API
# call. The files rarely change; no point re-reading them.
#
# Each tool's system prompt = the shared translation logic + that tool's mode
# skill, concatenated shared-FIRST (the mode skill references shared by path, so
# shared must sit above it in context).
_TRANSLATION_PROMPT = _load_skill("translate-shared.md")   # shared base
_TEXT_SKILL = _load_skill("translate-text-pdf.md")         # text-mode craft
_IMAGE_SKILL = _load_skill("translate-image-pdf.md")       # image-mode craft

_TEXT_SYSTEM_PROMPT = _TRANSLATION_PROMPT + "\n\n" + _TEXT_SKILL
_IMAGE_SYSTEM_PROMPT = _TRANSLATION_PROMPT + "\n\n" + _IMAGE_SKILL

# agent_routing_prompt.md — routing/traversal logic for the agent loop; loaded
# here ready for when that loop is built, not wired into these calls. It lives at
# the repo root (not skills/), so it keeps its own inline load.
_ROUTING_PROMPT = (
    _PROJECT_ROOT / "agent_routing_prompt.md"
).read_text(encoding="utf-8")

# Used by the thin wrappers (translate_one.py, translate_image_pdf.py)
# to classify files by name into lecture / tutorial / homework / exam.
# Each tuple is: ([Hebrew and English keywords to look for], type_string)
_TYPE_KEYWORDS = [
    (["הרצאה", "lecture", "lec"], "lecture"),
    (["תרגול", "tutorial", "tirgul"], "tutorial"),
    (["עבודה", "תרגיל", "homework", "hw"], "homework"),
    (["בוחן", "exam", "moed"], "exam"),
]

# Maps the semantic type value to the subfolder name inside the course folder:
# "lecture" → <vault>/<course>/Lectures/<file>_EN.md, etc. "reference" maps to
# "" and lands directly in the course root.
# Moved here from a retired single-file dispatch script — it was the only entry
# point that routed output into type-based subfolders; vault_output_path() below
# is the reusable form for the agent loop and any future entry point.
TYPE_TO_FOLDER = {
    "lecture":   "Lectures",
    "tutorial":  "Tutorials",
    "homework":  "Homework",
    "exam":      "Exams",
    "reference": "",   # saved directly in the course root folder
}

# ── Shared helpers ─────────────────────────────────────────────────────────────

def sha256_of(data: bytes) -> str:
    # We hash the raw PDF bytes (not the filename) so if the same file is
    # re-uploaded to Drive with a new name or ID, we still detect the duplicate.
    return "sha256:" + hashlib.sha256(data).hexdigest()


def infer_type(filename: str) -> str | None:
    # lower() so we match "Lecture", "LECTURE", "הרצאה" case-insensitively.
    # any() short-circuits: stops checking keywords as soon as one matches.
    lower = filename.lower()
    for keywords, type_val in _TYPE_KEYWORDS:
        if any(k in lower for k in keywords):
            return type_val
    return None  # caller decides what to do when type can't be inferred


def vault_output_path(
    vault_path: Path,
    course_english: str,
    type_value: str,
    drive_filename: str,
    custom_subfolder: str | None = None,
) -> Path:
    """Build the Obsidian output path for a translated file:

        <vault>/<course>/<type-subfolder>/<stem>_EN.md

    Subfolder resolution order:
      1. type_value in TYPE_TO_FOLDER → the mapped folder (lecture → Lectures, …;
         "reference" → "" → straight into the course root). Casing-guaranteed
         path for the four standard types.
      2. else if custom_subfolder is given → use it verbatim as the subfolder
         (escape hatch for one-off categories the standard types don't cover).
      3. else (unknown type, no custom_subfolder) → raise ValueError, so a
         garbled type still fails loudly rather than silently dumping to root.

    Path.stem strips the extension: "הרצאה 4.pdf" → "הרצאה 4".

    Pure path construction — does not touch the filesystem; the caller creates
    parent dirs and writes. Moved here from the retired single-file dispatch
    script, the only place that did type-based subfoldering.
    """
    if type_value in TYPE_TO_FOLDER:
        subfolder = TYPE_TO_FOLDER[type_value]
    elif custom_subfolder is not None:
        subfolder = custom_subfolder
    else:
        raise ValueError(
            f"unknown type {type_value!r}; expected one of "
            f"{list(TYPE_TO_FOLDER)} or pass custom_subfolder="
        )

    stem = Path(drive_filename).stem
    if subfolder:
        return vault_path / course_english / subfolder / f"{stem}_EN.md"
    return vault_path / course_english / f"{stem}_EN.md"


def _pil_to_base64_png(image) -> str:
    # Claude's API requires images as base64-encoded strings, not raw bytes.
    # Steps:
    # 1. image.save(buf, "PNG") — encode the PIL image into PNG bytes, write to buf
    # 2. buf.getvalue()         — pull the raw PNG bytes out of the buffer
    # 3. base64.b64encode()     — convert bytes → base64 bytes (still bytes, not str)
    # 4. .decode("utf-8")       — convert base64 bytes → str (what JSON needs)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")


# ── Cost calculation ───────────────────────────────────────────────────────────
# Identical formula for both text and image mode — no separate image-token
# accounting. Claude Opus 4.8 rates: $5/M input tokens, $25/M output tokens.
# Vision input tokens are priced the same as text input tokens; Anthropic
# converts images to tokens internally (~1600 tokens per 512×512 tile).

def _calc_cost(usage) -> float:
    return (usage.input_tokens * 5 + usage.output_tokens * 25) / 1_000_000


# ── Translation functions ──────────────────────────────────────────────────────

def translate_text_pdf(
    pdf_bytes: bytes,
    course_english: str,
    drive_file_id: str,
    drive_filename: str,
    source_hash: str,
    today_date: str,       # e.g. "2026-05-18" — injected so Claude can't fabricate it
) -> dict:
    """
    Extract text with pypdf, translate via Claude text API.
    Raises RuntimeError if extraction yields nothing usable.
    """
    # PdfReader needs a file-like object, not raw bytes.
    # io.BytesIO wraps the bytes in a seekable in-memory "file"
    # so pypdf can read it without touching the filesystem.
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))

    # Extract text from every page, join with blank lines between pages,
    # then strip leading/trailing whitespace from the whole block.
    # extract_text() returns None for image-only pages, so `or ""` avoids a crash.
    extracted = "\n\n".join(
        page.extract_text() or "" for page in reader.pages
    ).strip()

    # 50 chars is a conservative floor. A PDF with only a title would pass;
    # a blank or image-only PDF (where pypdf returned nothing) would fail.
    # The caller already routed via pdf_mode_detector signals, so this is a
    # last-resort guard rather than the primary routing decision.
    if len(extracted) < 50:
        raise RuntimeError(
            f"Text extraction returned only {len(extracted)} chars — "
            "run in image mode instead"
        )

    # anthropic.Anthropic() reads ANTHROPIC_API_KEY from os.environ automatically.
    # The caller must have run load_dotenv() before calling this function.
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=_TEXT_SYSTEM_PROMPT,   # translate-shared.md + translate-text-pdf.md
        messages=[
            {
                "role": "user",
                "content": (
                    # today_date goes first so it appears before the content,
                    # making it impossible for the model to miss or ignore it.
                    f"Today's date is {today_date}. "
                    "Translate the following Hebrew lecture per the system prompt.\n\n"
                    f"Course: {course_english}\n"
                    f"Source file: {drive_filename}\n\n"
                    f"{extracted}"
                ),
            }
        ],
    )

    usage = response.usage
    return {
        "markdown":      response.content[0].text,  # [0] because Claude always returns at least one TextBlock
        "input_tokens":  usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd":      round(_calc_cost(usage), 6),
        "model":         MODEL,
        "mode":          "text",
    }


def translate_image_pdf(
    pdf_bytes: bytes,
    course_english: str,
    drive_file_id: str,
    drive_filename: str,
    source_hash: str,
    today_date: str,       # e.g. "2026-05-18" — injected so Claude can't fabricate it
) -> dict:
    """
    Rasterise PDF pages at DPI, translate via Claude vision API.
    """
    # convert_from_bytes returns a list of PIL Image objects — one per page.
    # All pages are decoded into memory at once. A 10-page scan at 200 DPI
    # can be ~110 MB of raw pixel data before PNG encoding. If you hit
    # MemoryError, lower DPI to 150.
    images = convert_from_bytes(pdf_bytes, dpi=DPI)
    print(f"  Rasterised {len(images)} page(s) at {DPI} DPI.")

    # The Anthropic API accepts a "content array" — a list of blocks where each
    # block is either {"type": "text", "text": "..."} or
    # {"type": "image", "source": {...}}.
    # We put the instruction text block FIRST so Claude reads the date and
    # course context before processing the images.
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Today's date is {today_date}. "
                "Translate the following Hebrew lecture per the system prompt.\n\n"
                # The system prompt was written for text-mode and says
                # "mark missing figures as not_included". That default is wrong here
                # because Claude can actually see the figures. This overrides it.
                f"Source mode: vision (page images at {DPI} DPI). "
                "Describe figures, diagrams, and handwritten content directly "
                "from what you see. Do not mark figures as not_included — "
                "you can see them.\n\n"
                f"Course: {course_english}\n"
                f"Source file: {drive_filename}"
            ),
        }
    ]

    # Append one image block per page, after the instruction text.
    for i, img in enumerate(images):
        print(f"  Encoding page {i + 1}/{len(images)}...", end="\r", flush=True)
        content.append(
            {
                "type": "image",
                "source": {
                    # "base64" tells Claude to decode the data field from base64
                    # before processing. The alternative is "url" for a public
                    # image URL — we can't use that because our images only exist
                    # in memory and are never uploaded to the web.
                    "type":       "base64",
                    "media_type": "image/png",
                    "data":       _pil_to_base64_png(img),
                },
            }
        )
    print()  # the \r above overwrites the same terminal line; this moves past it

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=_IMAGE_SYSTEM_PROMPT,   # translate-shared.md + translate-image-pdf.md
        messages=[{"role": "user", "content": content}],
    )

    usage = response.usage
    return {
        "markdown":      response.content[0].text,
        "input_tokens":  usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd":      round(_calc_cost(usage), 6),
        "model":         MODEL,
        "mode":          "image",
    }


# ── Vault executor ──────────────────────────────────────────────────────────────

def save_to_vault(
    course_english: str,
    type_value: str,
    markdown: str,
    drive_file_id: str,
    drive_filename: str,
    source_hash: str,
    cost_data: dict,                 # {"model","cost_usd","input_tokens","output_tokens"}
    chosen_mode: str,                # "text" | "image"
    mode_reasoning: str,
    vault_path: Path,
    detection_signals: dict | None = None,
    custom_subfolder: str | None = None,
) -> dict:
    """Write the translated .md into the vault and record it in the manifest.

    Executor for a routing decision already made (course + type); it does not
    re-decide where the file goes. No LLM call. See skills/save-to-vault.md.

    Order is load-bearing: the .md is fully written and atomically renamed BEFORE
    the manifest is touched, so a failed disk write never leaves a manifest record
    for a file that isn't on disk. All manifest I/O is delegated to manifest.py.
    """
    # 1. Path — vault_output_path owns the type→folder mapping and the
    #    custom_subfolder escape hatch. Its ValueError (unknown type with no
    #    custom_subfolder) propagates from here, BEFORE any filesystem write —
    #    so a bad type leaves no .md and never touches the manifest.
    target = vault_output_path(
        vault_path, course_english, type_value, drive_filename,
        custom_subfolder=custom_subfolder,
    )

    # 2. Atomic .md write — temp file in the SAME directory as the target (same
    #    filesystem, so os.replace is a true atomic rename), UTF-8. Fully written
    #    and renamed before the manifest is touched.
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(markdown, encoding="utf-8")
    os.replace(tmp, target)

    md_path_relative = str(target.relative_to(vault_path))

    # 3. Manifest — only after the .md is safely on disk. upsert_entry matches by
    #    drive_file_id and replaces in place (manifest.py owns that contract).
    entry = {
        "drive_file_id":       drive_file_id,
        "drive_file_name":     drive_filename,
        "source_content_hash": source_hash,
        "md_path":             md_path_relative,
        "course":              course_english,
        "type":                type_value,
        "translated_at":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model":               cost_data["model"],
        "cost_usd":            cost_data["cost_usd"],
        "input_tokens":        cost_data["input_tokens"],
        "output_tokens":       cost_data["output_tokens"],
        "chosen_mode":         chosen_mode,
        "mode_reasoning":      mode_reasoning,
    }
    # Detection signals are written only when present — no null keys, so an
    # entry's lack of a signal is honest absence (e.g. manual entries never had one).
    if detection_signals is not None:
        entry.update(detection_signals)

    entries = manifest.load_log()
    entries = manifest.upsert_entry(entries, entry)
    manifest.save_log(entries)

    return {"status": "saved", "md_path": md_path_relative}
