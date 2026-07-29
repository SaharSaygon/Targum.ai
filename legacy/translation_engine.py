"""
translation_engine.py — the LEGACY (API pay-per-token) translation calls.

Both public functions have the same signature and return the same dict shape:
    {markdown, input_tokens, output_tokens, cost_usd, model, mode}

today_date (YYYY-MM-DD) is injected into every user message so Claude can
write an accurate date_translated frontmatter field without fabricating it.

2026-07-29 reorg: the no-LLM halves moved to core/ so the SDK subscription path
shares them — save_to_vault / vault_output_path / skill loading are in
core.vault, the rasterise-downscale-encode pipeline is in core.pdf_images.
`save_to_vault` and `vault_output_path` are re-exported here so legacy/agent.py
keeps calling `engine.save_to_vault(...)` unchanged.
"""

import io
import time

import anthropic
import pypdf

from core import costs
from core.pdf_images import DPI, render_pdf_pages_base64
from core.vault import (  # noqa: F401 — re-exported for legacy/agent.py
    TYPE_TO_FOLDER,
    image_system_prompt,
    save_to_vault,
    text_system_prompt,
    vault_output_path,
)

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL = "claude-opus-4-8"

# Image-mode output ceiling. A dense 20-plus-page handwritten lecture translates
# to more than the old 16000-token cap — several Lior lectures hit exactly 16000
# and were silently truncated mid-derivation. Current Opus models support up to
# 128K output tokens; we raise the ceiling well above any single lecture's real
# length. Cost is billed per token actually generated, so a high cap costs
# nothing extra on shorter files — it only stops the long ones from being cut
# off. Requests this large must STREAM (the SDK times out on non-streaming calls
# above ~16K), which translate_image_pdf does.
MAX_IMAGE_OUTPUT_TOKENS = 64000


# ── Cost calculation ───────────────────────────────────────────────────────────
# Delegates to costs.tiered_cost — the single cache-aware pricing source (also
# used by the routing ledger). Translation calls carry no cache tokens, so the
# tiered formula yields the same value as the old flat input*5+output*25. Vision
# input tokens are priced like text input; Anthropic tokenises images internally
# (~1600 tokens per 512×512 tile). Signature kept so callers/manifest are unaffected.

def _calc_cost(usage) -> float:
    return costs.tiered_cost(usage, MODEL)


# ── Translation functions ──────────────────────────────────────────────────────

def translate_text_pdf(
    pdf_bytes: bytes,
    course_english: str,
    drive_file_id: str,
    drive_filename: str,
    source_hash: str,
    today_date: str,       # e.g. "2026-05-18" — injected so Claude can't fabricate it
    model: str = MODEL,    # configured model; defaults to the module constant
    on_usage=None,         # optional callback(response, duration_ms) — cost-ledger hook
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
    _t0 = time.perf_counter()
    response = client.messages.create(
        model=model,
        max_tokens=16000,
        system=text_system_prompt(),   # translate-shared.md + translate-text-pdf.md (loaded per call)
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

    duration_ms = (time.perf_counter() - _t0) * 1000
    usage = response.usage
    if on_usage is not None:
        on_usage(response, duration_ms)
    return {
        "markdown":      response.content[0].text,  # [0] because Claude always returns at least one TextBlock
        "input_tokens":  usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd":      round(_calc_cost(usage), 6),
        "model":         model,
        "mode":          "text",
    }


def translate_image_pdf(
    pdf_bytes: bytes,
    course_english: str,
    drive_file_id: str,
    drive_filename: str,
    source_hash: str,
    today_date: str,       # e.g. "2026-05-18" — injected so Claude can't fabricate it
    model: str = MODEL,    # configured model; defaults to the module constant
    on_usage=None,         # optional callback(response, duration_ms) — cost-ledger hook
) -> dict:
    """
    Rasterise PDF pages at DPI, translate via Claude vision API.
    """
    pages = render_pdf_pages_base64(pdf_bytes)

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
    for page_b64 in pages:
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
                    "data":       page_b64,
                },
            }
        )

    client = anthropic.Anthropic()
    _t0 = time.perf_counter()
    # Stream the response: MAX_IMAGE_OUTPUT_TOKENS is far above the SDK's
    # non-streaming timeout threshold (~16K), so a plain messages.create would
    # raise before the model finished a long lecture. messages.stream holds the
    # connection open; get_final_message reassembles the full response (with
    # usage) once the stream completes — same return shape as create().
    with client.messages.stream(
        model=model,
        max_tokens=MAX_IMAGE_OUTPUT_TOKENS,
        system=image_system_prompt(),   # translate-shared.md + translate-image-pdf.md (loaded per call)
        messages=[{"role": "user", "content": content}],
    ) as stream:
        response = stream.get_final_message()

    duration_ms = (time.perf_counter() - _t0) * 1000
    usage = response.usage
    if on_usage is not None:
        on_usage(response, duration_ms)
    return {
        "markdown":      response.content[0].text,
        "input_tokens":  usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cost_usd":      round(_calc_cost(usage), 6),
        "model":         model,
        "mode":          "image",
    }
