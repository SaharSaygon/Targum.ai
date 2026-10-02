"""
sessions.py — the two per-file Claude Agent SDK sessions (Shape D), plus the
upfront course-naming session.

ROUTE session   — system = agent_routing_prompt.md ONLY. Decides skip vs
                  translate (+mode/course/type) for ONE file from its path and
                  extraction signals. All routing judgment stays with the model;
                  the decision comes back as structured output. Skip-only files
                  never load a single skill token.

TRANSLATE session — system = the translation skills ONLY (translate-shared.md +
                  the one mode skill — exactly the legacy engine's composition,
                  loaded just-in-time). Writes the finished markdown straight to
                  the vault with the Write tool, so no single-response output
                  cap can truncate a long lecture. On refusal it writes nothing
                  and reports REFUSED: <reason> (the standing contract).

Both session types share a stable system-prompt prefix within their type, so
with several files in flight the prompt cache stays warm for each.
"""

import io
from datetime import datetime, timezone
from pathlib import Path

import pypdf
from claude_agent_sdk import (
    ClaudeAgentOptions,
    ResultMessage,
    create_sdk_mcp_server,
    query,
    tool,
)

from core.paths import ROUTING_PROMPT_PATH
from core.pdf_images import DPI, render_pdf_pages_base64
from core.vault import TYPE_TO_FOLDER, image_system_prompt, text_system_prompt

# ── Structured-output schemas ─────────────────────────────────────────────────

ROUTE_DECISION_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["skip", "translate"]},
            "skip_reason": {
                "type": ["string", "null"],
                "description": "Required when action=skip: the rule that fired.",
            },
            "mode": {"type": ["string", "null"], "enum": ["text", "image", None]},
            "course_english": {"type": ["string", "null"]},
            "file_type": {
                "type": ["string", "null"],
                "enum": [*TYPE_TO_FOLDER.keys(), "other", None],
            },
            "custom_subfolder": {
                "type": ["string", "null"],
                "description": "Only with file_type=other: verbatim subfolder name.",
            },
            "reasoning": {"type": "string"},
        },
        "required": [
            "action", "skip_reason", "mode", "course_english",
            "file_type", "custom_subfolder", "reasoning",
        ],
        "additionalProperties": False,
    },
}

COURSE_NAMING_SCHEMA = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "hebrew": {"type": "string"},
                        "english": {"type": "string"},
                    },
                    "required": ["hebrew", "english"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["mappings"],
        "additionalProperties": False,
    },
}


# ── Internals ─────────────────────────────────────────────────────────────────

async def _run(options: ClaudeAgentOptions, prompt) -> ResultMessage:
    """Drive one session to completion and return its ResultMessage."""
    result = None
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, ResultMessage):
            result = message
    if result is None:
        raise RuntimeError("session ended without a ResultMessage")
    return result


def _signals_server(ctx):
    """In-process MCP server exposing fetch_signal_detail for THIS file only —
    the offloaded per-page detail the route session may pull for a genuinely
    ambiguous mode decision (most files never need it)."""

    @tool(
        "fetch_signal_detail",
        "Return the full per-page extraction signals and the unrecognized-text "
        "sample for the file under decision. Call only when the scalar signals "
        "are genuinely ambiguous for the text-vs-image choice.",
        {},
    )
    async def fetch_signal_detail(args):
        import json
        return {"content": [{
            "type": "text",
            "text": json.dumps(ctx.signals_full, ensure_ascii=False),
        }]}

    return create_sdk_mcp_server(name="signals", tools=[fetch_signal_detail])


# ── ROUTE session ─────────────────────────────────────────────────────────────

def _route_prompt(ctx, courses_block: str, extra_context: str | None) -> str:
    parts = [
        "You are routing ONE file from the deterministic pre-pass worklist. "
        "Everything up to the routing decision has already happened in code: "
        "the file is downloaded, deduped against the manifest, and its "
        "extraction signals are computed (below). In THIS session you only "
        "DECIDE, per your routing rules: skip vs translate, and when "
        "translating, the mode (text/image), English course name, and file "
        "type. Do NOT translate and do NOT try to call read_file, "
        "translate_*, save_to_vault, update_mapping, or skip_file — those do "
        "not exist here; translation and recording happen elsewhere based on "
        "your structured decision.",
        f"## File\npath: {ctx.path}\nfilename: {ctx.name}\nfile_id: {ctx.file_id}",
        f"## Extraction signals\n"
        f"page_count: {ctx.signals['page_count']}\n"
        f"file_size_kb: {ctx.signals['file_size_kb']}\n"
        f"recognizability: {ctx.signals['recognizability']}\n"
        f"tokens_per_page: {ctx.signals['tokens_per_page']}\n"
        f"bytes_per_token: {ctx.signals['bytes_per_token']}\n"
        f"math_token_fraction: {ctx.signals['math_token_fraction']}\n"
        f"max_garbage_run_DIAGNOSTIC: {ctx.signals['max_garbage_run_DIAGNOSTIC']}\n"
        "(fetch_signal_detail returns the per-page breakdown if the scalars "
        "are genuinely ambiguous.)",
        f"## Approved course mappings (from courses.json)\n{courses_block}",
    ]
    if extra_context:
        parts.append(f"## Additional context from a previous attempt\n{extra_context}")
    parts.append(
        "Decide now and return the structured decision. skip_reason only for "
        "skips; mode/course_english/file_type only for translations "
        "(custom_subfolder only with file_type=other)."
    )
    return "\n\n".join(parts)


async def route_file(ctx, courses_block: str, model: str,
                     extra_context: str | None = None) -> tuple[dict, ResultMessage]:
    """Run the ROUTE session for one prepared file. Returns (decision, result)."""
    options = ClaudeAgentOptions(
        system_prompt=ROUTING_PROMPT_PATH.read_text(encoding="utf-8"),
        model=model,
        tools=[],                       # no built-in tools in a route session
        mcp_servers={"signals": _signals_server(ctx)},
        strict_mcp_config=True,
        allowed_tools=["mcp__signals__fetch_signal_detail"],
        permission_mode="dontAsk",      # deny anything not pre-approved
        max_turns=6,
        output_format=ROUTE_DECISION_SCHEMA,
    )
    result = await _run(options, _route_prompt(ctx, courses_block, extra_context))
    decision = result.structured_output
    if not isinstance(decision, dict) or "action" not in decision:
        raise RuntimeError(f"route session returned no usable decision: {result.result!r}")
    return decision, result


# ── TRANSLATE session ─────────────────────────────────────────────────────────

def extract_pdf_text(pdf_bytes: bytes) -> str:
    """pypdf extraction with the legacy 50-char floor. Raises RuntimeError on
    an unusable yield (caller feeds that back to a route session, which then
    decides image-retry vs skip — same recovery as the legacy path)."""
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    extracted = "\n\n".join(page.extract_text() or "" for page in reader.pages).strip()
    if len(extracted) < 50:
        raise RuntimeError(
            f"Text extraction returned only {len(extracted)} chars — "
            "run in image mode instead"
        )
    return extracted


def _write_instruction(target: Path) -> str:
    return (
        "Write the COMPLETE translated markdown document to this exact "
        f"absolute path using the Write tool:\n{target}\n"
        "Do not output the translation as chat text. If the document is very "
        "long, write it in sequential parts: Write the opening portion first, "
        "then append each continuation with the Edit tool (old_string = the "
        "file's current final line, new_string = that line followed by the "
        "continuation).\n"
        "If per the system prompt you must REFUSE, do NOT write or edit any "
        "file — reply with a final message whose FIRST line is exactly "
        "'REFUSED: <reason>'. Otherwise, after the file is fully written, "
        "reply with the single word SAVED."
    )


async def translate_file(ctx, mode: str, course_english: str, target: Path,
                         model: str) -> tuple[str, ResultMessage | None]:
    """Run the TRANSLATE session. Returns (status, result) where status is
    "saved" | "refused: <reason>" | "text_extraction_failed: <reason>".
    The .md is written by the session itself; the caller verifies and records.
    """
    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if mode == "text":
        try:
            extracted = extract_pdf_text(ctx.pdf_bytes)
        except RuntimeError as e:
            return f"text_extraction_failed: {e}", None
        system = text_system_prompt()
        content = [{
            "type": "text",
            "text": (
                f"Today's date is {today_date}. "
                "Translate the following Hebrew lecture per the system prompt.\n\n"
                f"Course: {course_english}\n"
                f"Source file: {ctx.name}\n\n"
                f"{extracted}\n\n"
                f"{_write_instruction(target)}"
            ),
        }]
    else:
        system = image_system_prompt()
        content = [{
            "type": "text",
            "text": (
                f"Today's date is {today_date}. "
                "Translate the following Hebrew lecture per the system prompt.\n\n"
                f"Source mode: vision (page images at {DPI} DPI). "
                "Describe figures, diagrams, and handwritten content directly "
                "from what you see. Do not mark figures as not_included — "
                "you can see them.\n\n"
                f"Course: {course_english}\n"
                f"Source file: {ctx.name}\n\n"
                f"{_write_instruction(target)}"
            ),
        }]
        for page_b64 in render_pdf_pages_base64(ctx.pdf_bytes):
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": "image/png", "data": page_b64},
            })

    async def _prompt_stream():
        yield {"type": "user", "message": {"role": "user", "content": content}}

    # The CLI refuses to start in a cwd that doesn't exist, and a NEW course's
    # vault folder only comes into being when its first file is written — so
    # create it up front or the first file of every new course fails forever.
    target.parent.mkdir(parents=True, exist_ok=True)

    options = ClaudeAgentOptions(
        system_prompt=system,
        model=model,
        tools=["Write", "Edit"],
        allowed_tools=["Write", "Edit"],
        permission_mode="acceptEdits",
        cwd=str(target.parent),
        max_turns=20,
    )
    result = await _run(options, _prompt_stream())

    final_text = (result.result or "").strip()
    if final_text.startswith("REFUSED:"):
        return f"refused: {final_text[len('REFUSED:'):].strip()}", result
    return "saved", result


# ── Course-naming session (upfront, before the fan-out) ───────────────────────

async def resolve_course_names(unmapped: list[str], courses_block: str,
                               model: str) -> tuple[dict[str, str], ResultMessage]:
    """ONE session that names every unmapped course folder before the
    concurrent fan-out, so parallel route sessions can never invent divergent
    names for the same new course. Returns ({hebrew: english}, result)."""
    folder_list = "\n".join(f"- {h}" for h in unmapped)
    prompt = (
        "These Google Drive course folders have no approved English mapping in "
        "courses.json. Apply your course auto-naming rules and name each one. "
        "Return ONLY the structured mappings — do not translate anything and do "
        "not call any tools.\n\n"
        f"## Unmapped course folders\n{folder_list}\n\n"
        f"## Existing approved mappings (style reference)\n{courses_block}"
    )
    options = ClaudeAgentOptions(
        system_prompt=ROUTING_PROMPT_PATH.read_text(encoding="utf-8"),
        model=model,
        tools=[],
        permission_mode="dontAsk",
        max_turns=3,
        output_format=COURSE_NAMING_SCHEMA,
    )
    result = await _run(options, prompt)
    out = result.structured_output or {}
    mappings = {m["hebrew"]: m["english"] for m in out.get("mappings", [])}
    return mappings, result
