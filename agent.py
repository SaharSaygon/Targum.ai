# agent.py — the agent loop, 7 tool schemas, and one handler per tool.
import json
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from anthropic import Anthropic

import costs
import courses
import dedup
import drive
import manifest
import translation_engine as engine
from pdf_mode_detector import detect_pdf_mode

load_dotenv()
client = Anthropic()

ROOT_FOLDER_ID = "1FoM5o24yoBJuvpdtoZ0waJGosS4wef6V"  # Semester ד׳ 2026 course root
SYSTEM_PROMPT = Path("agent_routing_prompt.md").read_text(encoding="utf-8")


# The 7 tool schemas. The `description` is how the model learns what each
# tool does — write it for the model, not for you. input_schema is JSON Schema.
TOOLS = [
    {
        "name": "list_folder",
        "description": "List the immediate children of a Google Drive folder. Returns each child's name, id, whether it's a file or folder, and mime_type. Your eyes on the tree — call this to see what's inside a folder before deciding what to do.",
        "input_schema": {
            "type": "object",
            "properties": {
                "folder_id": {"type": "string", "description": "Google Drive folder ID to list."}
            },
            "required": ["folder_id"],
        },
    },
    {
        "name": "read_file",
        "description": "Download a Drive file, hash it, check the dedup manifest, and run the extraction detector. Returns status 'already_done' (skip it), 'ready' with the SCALAR extraction signals (recognizability, tokens_per_page, bytes_per_token, math_token_fraction, max_garbage_run_DIAGNOSTIC, page_count, file_size_kb) — sufficient for the text-vs-image call — plus a signals_full_handle. The verbose per_page array and unrecognized_sample are offloaded behind that handle (fetch via fetch_signal_detail only for a genuinely ambiguous file). Or status 'error' (download/parse failed — log and move on). File content is cached internally by hash — you only get the signals, never the raw content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_id": {"type": "string", "description": "Google Drive file ID to read."}
            },
            "required": ["file_id"],
        },
    },
    {   # RETAINED 2026-06-06: 0 calls across Phase 1 (~97 files); scalar signals carried
        # every mode decision. Kept as fallback for future semesters with genuinely
        # ambiguous text-vs-image files. Drop if still unused after more real runs.
        "name": "fetch_signal_detail",
        "description": "Fetch the verbose extraction-signal detail (per_page array + unrecognized_sample) for a file by the signals_full_handle that read_file returned. These are offloaded from read_file's result to keep context lean. The scalar signals in the read_file result resolve the text-vs-image mode for almost every file — call this ONLY for a genuinely ambiguous file where the scalars don't settle it (e.g. to inspect per-page token variance for a typed-over-handwritten hybrid, or the garbage shape to tell handwriting from typed-extraction failure). Default to image on ambiguity rather than fetching.",
        "input_schema": {
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "The signals_full_handle from a read_file 'ready' result."}
            },
            "required": ["handle"],
        },
    },
    {
        "name": "translate_text_pdf",
        "description": "Translate a text-extractable PDF (typed, pypdf-readable). Reads the cached content by source_hash. Use when the read_file signals indicate typed text. Returns translated markdown (cached by hash) plus cost/token data. May return status 'refused' if the extracted text is actually OCR garbage — if so, reconsider image mode or flag.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_hash": {"type": "string", "description": "Hash returned by read_file, identifies the cached content."},
                "course": {"type": "string", "description": "English course name."},
                "drive_filename": {"type": "string", "description": "Original Drive filename."},
                "mode_reasoning": {"type": "string", "description": "One line: which signals drove the text-mode choice."},
            },
            "required": ["source_hash", "course", "drive_filename", "mode_reasoning"],
        },
    },
    {
        "name": "translate_image_pdf",
        "description": "Translate a handwritten/scanned/formula-dense PDF via vision (pages rendered to images). Reads cached content by source_hash. Use when signals indicate handwriting, low text yield, or math-dominant pages. The safer default when mode is ambiguous — text mode silently fabricates on handwritten content. Returns translated markdown (cached) plus cost/token data.",
        "input_schema": {
            "type": "object",
            "properties": {
                "source_hash": {"type": "string", "description": "Hash returned by read_file."},
                "course": {"type": "string", "description": "English course name."},
                "drive_filename": {"type": "string", "description": "Original Drive filename."},
                "mode_reasoning": {"type": "string", "description": "One line: which signals drove the image-mode choice."},
            },
            "required": ["source_hash", "course", "drive_filename", "mode_reasoning"],
        },
    },
    {
        "name": "save_to_vault",
        "description": "Write a completed translation to the Obsidian vault and record it in the manifest. file_type is one of lecture/tutorial/homework/exam, OR pass custom_subfolder for a genuine non-standard file (formula sheet, syllabus). Returns the vault-relative path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "course": {"type": "string"},
                "file_type": {"type": "string", "enum": ["lecture", "tutorial", "homework", "exam"], "description": "Standard type. Omit and use custom_subfolder for non-standard files."},
                "source_hash": {"type": "string"},
                "md_cache_handle": {"type": "string", "description": "Handle to the translated markdown returned by a translate tool."},
                "drive_file_id": {"type": "string"},
                "drive_filename": {"type": "string"},
                "chosen_mode": {"type": "string", "enum": ["text", "image"]},
                "mode_reasoning": {"type": "string"},
                "custom_subfolder": {"type": "string", "description": "For non-standard files only. Reuse existing custom names; don't invent near-duplicates."},
            },
            "required": ["course", "source_hash", "md_cache_handle", "drive_file_id", "drive_filename", "chosen_mode", "mode_reasoning"],
        },
    },
    {
        "name": "update_mapping",
        "description": "Persist an approved Hebrew→English course-name mapping to courses.json so later runs stay consistent. Call this after you auto-assign an English name to a course folder that wasn't in the kickoff mappings — write the name you chose, then continue translating that course's files. No approval gate: you own the naming decision. Use the exact Hebrew folder name as it appears in Drive.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hebrew_name": {"type": "string", "description": "The course's Hebrew folder name, exactly as it appears in Drive."},
                "english_name": {"type": "string", "description": "The English course name you auto-assigned."},
            },
            "required": ["hebrew_name", "english_name"],
        },
    },
]

# --- handlers: one per tool. list_folder delegates to drive.py. ---

def handle_list_folder(inp):
    children = drive.list_folder_children(inp["folder_id"])
    return json.dumps(children, ensure_ascii=False)

# In-memory content cache: source_hash → raw PDF bytes. Lives in the loop's
# scope. Keyed by CONTENT hash (not file_id) so a re-edited source — same
# file_id, new bytes — misses the stale entry. The translate handlers read
# the bytes back out of here by source_hash; bytes never enter a tool result.
CONTENT_CACHE = {}

# ── Cost-ledger run context ───────────────────────────────────────────────────
# Set by __main__ before the loop; stay None/0/[] when agent.py is imported (for
# tests) or run without a ledger — the translation recorder skips when LEDGER_PATH
# is None. RUN_ID derives from the same timestamp as logs/agent_<ts>.log so the
# log and the ledger correlate. LEDGER_ROWS accumulates rows IN MEMORY so the
# crash-safe summary reports totals without re-reading a possibly-partial file.
LEDGER_PATH = None
RUN_ID = None
CURRENT_TURN = 0
LEDGER_ROWS = []


def read_file_logic(file_id):
    """Download → hash → dedup → detect. Returns a result dict (the handler
    json.dumps it). Never raises into the loop: download/parse/detector failures
    come back as {"status": "error", ...}."""
    # 0. md5 freshness gate (sync-immune download-skip). Fetch Drive's content
    #    checksum — a cheap metadata call, NO byte download. If this file is
    #    already done with a stored source_md5 that matches, the bytes are
    #    provably unchanged → return already_done WITHOUT downloading. This sits
    #    IN FRONT of the SHA dedup as an optimization; SHA stays the authority on
    #    translate-vs-done (md5 only governs skip-the-download). Drive md5 is None
    #    for native Google Docs → gate N/A, fall through to the download path.
    try:
        drive_md5 = drive.file_md5(file_id)
    except Exception as e:
        return {"status": "error", "reason": f"metadata fetch failed: {e}"}
    entries = manifest.load_log()
    verdict = dedup.md5_gate(entries, file_id, drive_md5)
    if verdict is not None:
        return verdict

    # 1. download the raw bytes (integrity-checked vs Drive size)
    try:
        pdf_bytes = drive.download_bytes(file_id)
    except Exception as e:
        return {"status": "error", "reason": f"download failed: {e}"}

    # 2. content hash — identity for dedup AND cache key. Canonical "sha256:..."
    #    format from manifest, so it compares verbatim against source_content_hash.
    source_hash = manifest.sha256_of(pdf_bytes)

    # 3. dedup against the manifest: by drive_file_id (gated on hash match), with a
    #    cross-ID content fallback for the same bytes under another id. Pure
    #    decision — see dedup.hash_dedup. already_done → return; else process fresh.
    #    Safe now that Step 1's integrity check guarantees source_hash came from
    #    COMPLETE bytes — without that, cross-ID could lock in a truncated file.
    verdict = dedup.hash_dedup(entries, file_id, source_hash)
    if verdict.get("status") == "already_done":
        return verdict
    # no manifest match, OR the source bytes changed (re-edit) → process fresh

    # 4. extraction signals (detector swallows its own pypdf errors → zero-yield;
    #    wrap anyway so an unexpected throw doesn't kill the loop)
    try:
        signals = detect_pdf_mode(pdf_bytes)
    except Exception as e:
        return {"status": "error", "reason": f"detector failed: {e}"}

    # 5. cache the bytes by content hash for the translate tools, and a LEAN
    #    scalar subset of signals under "<hash>:signals" for save_to_vault to log
    #    into the manifest (path (a): cache, don't round-trip through the agent).
    #    The verbose per_page list + unrecognized_sample are DROPPED from the
    #    manifest-bound copy — but stay in the RETURN below so the agent keeps the
    #    full picture for text-vs-image reasoning. Two shapes: rich to the agent,
    #    lean to the manifest.
    CONTENT_CACHE[source_hash] = pdf_bytes
    # Drive md5 (from the gate fetch above) cached for save_to_vault to record as
    # source_md5 → the freshness gate can fast-path this file next time.
    CONTENT_CACHE[f"{source_hash}:md5"] = drive_md5
    CONTENT_CACHE[f"{source_hash}:signals"] = {
        "recognizability":            signals["recognizability"],
        "tokens_per_page":            signals["tokens_per_page"],
        "bytes_per_token":            signals["bytes_per_token"],
        "math_token_fraction":        signals["math_token_fraction"],
        "max_garbage_run_DIAGNOSTIC": signals["max_garbage_run_DIAGNOSTIC"],
        "page_count":                 signals["page_count"],
        "file_size_kb":               signals["file_size_kb"],
    }
    # Context-offload: the verbose parts (per_page array + unrecognized_sample)
    # are the bloat that would accumulate in message history every turn. Stash
    # them behind a handle (same cache@hash discipline as bytes/md) and return
    # only the scalars + the handle. The agent decides mode from the scalars and
    # can fetch this detail via fetch_signal_detail(handle) for a genuinely
    # ambiguous file. This shrinks the result AT CREATION — past results in
    # history are never rewritten, so the cached prefix stays byte-stable.
    signals_full_handle = f"{source_hash}:signals_full"
    CONTENT_CACHE[signals_full_handle] = {
        "per_page":            signals["per_page"],
        "unrecognized_sample": signals["unrecognized_sample"],
    }

    # 6. ready — scalar signals only (enough for the text-vs-image call); the
    #    verbose per_page/unrecognized_sample are offloaded behind the handle.
    return {
        "status": "ready",
        "source_hash": source_hash,
        "page_count": signals["page_count"],
        "file_size_kb": signals["file_size_kb"],
        "signals": {
            "recognizability": signals["recognizability"],
            "tokens_per_page": signals["tokens_per_page"],
            "bytes_per_token": signals["bytes_per_token"],
            "math_token_fraction": signals["math_token_fraction"],
            "max_garbage_run_DIAGNOSTIC": signals["max_garbage_run_DIAGNOSTIC"],
        },
        "signals_full_handle": signals_full_handle,
    }


def handle_read_file(inp):
    return json.dumps(read_file_logic(inp["file_id"]), ensure_ascii=False)


def handle_fetch_signal_detail(inp):
    """Return the offloaded per_page + unrecognized_sample for a read_file handle.
    The agent calls this only for ambiguous mode decisions; most files never need it."""
    detail = CONTENT_CACHE.get(inp["handle"])
    if detail is None:
        return json.dumps(
            {"status": "error", "reason": f"no signal detail cached for handle {inp['handle']}"},
            ensure_ascii=False)
    return json.dumps({"status": "ok", **detail}, ensure_ascii=False)


def _translate_logic(inp, engine_fn, category):
    """Shared core for both translate tools — they differ only in which engine
    function (text vs vision path) they call. Reads the cached source bytes,
    runs the engine, caches the markdown under a handle, returns metadata only.
    The markdown — like the bytes — never enters the tool result.

    `category` is the ledger bucket ("translation_text"/"translation_image"); the
    engine reports its API usage through the on_usage callback below, and the
    agent (which holds the run context) writes the ledger row."""
    def _on_usage(response, duration_ms):
        # engine has no run context — record here where RUN_ID/turn are known.
        if LEDGER_PATH is None:
            return
        LEDGER_ROWS.append(
            costs.record_call(LEDGER_PATH, RUN_ID, CURRENT_TURN, category, response, duration_ms)
        )

    source_hash = inp["source_hash"]
    # Cache miss means the agent reached a translate tool without read_file
    # caching this source first. Report it, don't crash the loop.
    if source_hash not in CONTENT_CACHE:
        return {"status": "error",
                "reason": f"cache miss for {source_hash} — read_file must run first"}

    pdf_bytes = CONTENT_CACHE[source_hash]
    # Code-injected date (Issue 2 fix): the model can't fabricate date_translated
    # because the engine stamps today's date into the prompt itself.
    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        result = engine_fn(
            pdf_bytes,
            inp["course"],
            "",                      # drive_file_id — unused by the engine's translate path
            inp["drive_filename"],
            source_hash,
            today_date,
            on_usage=_on_usage,      # engine emits (response, duration_ms) → ledger
        )
    except RuntimeError as e:
        # Text path's refuse-rather-than-reconstruct backstop: extraction yielded
        # nothing usable (<50 chars). Tell the agent to reconsider image mode.
        return {"status": "refused", "reason": str(e)}
    except Exception as e:
        # Any other engine failure (e.g. rasterisation) — keep the loop alive.
        return {"status": "error", "reason": f"translation failed: {e}"}

    markdown = result["markdown"]
    cost_data = {
        "input_tokens":  result["input_tokens"],
        "output_tokens": result["output_tokens"],
        "cost_usd":      result["cost_usd"],
        "model":         result["model"],
    }

    # Structural content refusal (the L4 guardrail): the skill contract requires
    # a genuine refuse-rather-than-reconstruct to put "REFUSED: <reason>" as the
    # VERY FIRST LINE. We check first-line-only, so a body that merely discusses
    # OCR garbage / refusal can't trip it. This composes with the extraction-yield
    # RuntimeError above — both content-garbage and zero-yield surface as refused.
    stripped = markdown.lstrip()
    if stripped.startswith("REFUSED:"):
        reason = stripped[len("REFUSED:"):].split("\n", 1)[0].strip()
        # No translation happened → do NOT cache, return no handle. cost_data is
        # kept: the model WAS called, so the run log still captures the spend.
        return {"status": "refused", "reason": reason, "cost_data": cost_data}

    # Cache the markdown by a handle derived from source_hash; return the handle,
    # not the content (same rule as the raw bytes). Also cache cost_data under
    # "<hash>:cost" so save_to_vault writes real token counts into the manifest
    # without the agent forwarding them through message history (path (a)).
    md_handle = f"{source_hash}:md"
    CONTENT_CACHE[md_handle] = markdown
    CONTENT_CACHE[f"{source_hash}:cost"] = cost_data

    return {
        "status": "translated",
        "md_cache_handle": md_handle,
        "chosen_mode": result["mode"],
        "cost_data": cost_data,
    }


def handle_translate_text(inp):
    return json.dumps(_translate_logic(inp, engine.translate_text_pdf, "translation_text"), ensure_ascii=False)


def handle_translate_image(inp):
    return json.dumps(_translate_logic(inp, engine.translate_image_pdf, "translation_image"), ensure_ascii=False)


def handle_save_to_vault(inp):
    """Thin wrapper over translation_engine.save_to_vault. Reads the translated
    markdown by handle and the cached cost_data/signals by source_hash (path (a):
    neither round-trips through the agent), then delegates the atomic .md write +
    in-place manifest upsert to the engine."""
    md = CONTENT_CACHE.get(inp["md_cache_handle"])
    if md is None:
        return json.dumps(
            {"status": "error",
             "reason": f"md cache miss for {inp['md_cache_handle']} — translate must run first"},
            ensure_ascii=False)

    source_hash = inp["source_hash"]
    cost_data = CONTENT_CACHE.get(f"{source_hash}:cost")
    if cost_data is None:
        return json.dumps(
            {"status": "error",
             "reason": f"cost cache miss for {source_hash} — translate must run first"},
            ensure_ascii=False)
    # signals + md5 are optional: omitted from the manifest when absent (the engine
    # handles None). present for any file that went through read_file. The md5 lets
    # read_file's freshness gate fast-path this file (skip the download) next time.
    signals = CONTENT_CACHE.get(f"{source_hash}:signals")
    source_md5 = CONTENT_CACHE.get(f"{source_hash}:md5")

    try:
        result = engine.save_to_vault(
            course_english=inp["course"],
            type_value=inp.get("file_type"),      # None when routing via custom_subfolder
            markdown=md,
            drive_file_id=inp["drive_file_id"],
            drive_filename=inp["drive_filename"],
            source_hash=source_hash,
            cost_data=cost_data,
            chosen_mode=inp["chosen_mode"],
            mode_reasoning=inp["mode_reasoning"],
            vault_path=Path(os.environ["OBSIDIAN_VAULT_PATH"]),
            detection_signals=signals,
            source_md5=source_md5,
            custom_subfolder=inp.get("custom_subfolder"),
        )
    except ValueError as e:
        # Unknown file_type with no custom_subfolder — a garbled type is a real
        # error, not license to guess (the locked save_to_vault contract).
        return json.dumps({"status": "error", "reason": str(e)}, ensure_ascii=False)

    return json.dumps(result, ensure_ascii=False)
def handle_update_mapping(inp):
    """Persist an agent-assigned course mapping to courses.json (no approval
    gate — the autonomy reversal). Firing this means the agent auto-named a
    course that had no prior mapping, so we log it LOUDLY: this print is the
    decision-log/audit line that makes the autonomy bet reversible. Stage 3 will
    route it to a per-run log file; for now it must be visible on stdout."""
    hebrew = inp["hebrew_name"]
    english = inp["english_name"]
    result = courses.update_mapping(hebrew, english)
    print(f"AUTO-NAMED: {hebrew} → {english} (no prior mapping, agent-assigned)")
    return json.dumps({"status": "mapped", **result}, ensure_ascii=False)

# name → handler. The loop looks the tool up here by its name.
HANDLERS = {
    "list_folder":         handle_list_folder,
    "read_file":           handle_read_file,
    "fetch_signal_detail": handle_fetch_signal_detail,
    "translate_text_pdf":  handle_translate_text,
    "translate_image_pdf": handle_translate_image,
    "save_to_vault":       handle_save_to_vault,
    "update_mapping":      handle_update_mapping,
}

# ── Prompt caching for the routing loop ───────────────────────────────────────
# Render order is tools → system → messages. The routing call re-sends the static
# system prompt + tool schemas AND the whole growing conversation every turn; with
# no caching each turn re-pays full input price for all of it (the dominant cost on
# a long run). Two ephemeral breakpoints fix that:
#   1. SYSTEM_CACHED — a breakpoint on the system block caches tools+system together.
#   2. a breakpoint on the LAST message block (moved forward each turn) caches the
#      conversation prefix, so each turn pays full price only for that turn's new
#      blocks. Cache reads cost ~0.1× input; writes ~1.25×.
SYSTEM_CACHED = [{
    "type": "text",
    "text": SYSTEM_PROMPT,
    "cache_control": {"type": "ephemeral"},
}]


def _set_cache_breakpoint(messages):
    """Move the conversation-prefix cache breakpoint onto the last message's last
    block. Clears any prior message-level breakpoint first so the request never
    exceeds the 4-breakpoint cap (we use 2: system + this one). cache_control is
    metadata, not part of the cached bytes — moving it leaves the prefix identical,
    so earlier turns' cache writes stay readable. Assistant blocks are SDK objects
    (not dicts) and are skipped; only tool_result dicts / the wrapped kickoff are
    marked."""
    for m in messages:
        content = m["content"]
        if isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    block.pop("cache_control", None)
    content = messages[-1]["content"]
    if isinstance(content, str):
        # kickoff turn: wrap the string in a text block so it can carry the marker
        messages[-1]["content"] = [{
            "type": "text", "text": content,
            "cache_control": {"type": "ephemeral"},
        }]
    elif isinstance(content, list) and content and isinstance(content[-1], dict):
        content[-1]["cache_control"] = {"type": "ephemeral"}


# --- run the loop only when executed directly; importable for tests ---
if __name__ == "__main__":
    # --- approved course mappings: read from courses.json (flat {hebrew: english}),
    #     injected into the kickoff so the agent reads them from run context. ---
    COURSES = courses.load_courses()
    mappings_block = "\n".join(
        f"- {hebrew} → {english}" for hebrew, english in COURSES.items()
    )
    print("=== Approved course mappings block ===")
    print(mappings_block)
    print("======================================")

    # --- per-run file logger: durable audit trail at logs/agent_<ts>.log.
    #     logs/ and *.log are gitignored — never committed. stdout prints stay
    #     (watch live); the file is the permanent record of every decision. ---
    run_start = datetime.now(timezone.utc)
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    run_id = run_start.strftime('%Y%m%d_%H%M%S')   # shared by the run log + the ledger
    log_path = logs_dir / f"agent_{run_id}.log"
    log_f = open(log_path, "w", encoding="utf-8")
    print(f"Logging this run to {log_path}")

    # cost ledger: one JSON line per LLM call, correlated to this run by run_id.
    # Set the module-level run context so the translation recorder (_translate_logic,
    # invoked from the tool handlers) and the routing recorder below both find it.
    RUN_ID = run_id
    LEDGER_PATH = logs_dir / f"ledger_{run_id}.jsonl"
    print(f"Cost ledger: {LEDGER_PATH}")

    def log(msg):
        """Append a line to the run log, flushing so a killed run keeps its trail."""
        log_f.write(msg + "\n")
        log_f.flush()

    def log_trunc(value, limit=500):
        """Stringify and cap long values (signals dicts, md handles) so the log
        stays readable. Never receives full markdown or bytes — those live only
        in the cache, never in a tool result."""
        s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        return s if len(s) <= limit else s[:limit] + f"… [truncated {len(s) - limit} chars]"

    log(f"RUN START {run_start.strftime('%Y-%m-%d %H:%M:%S')} UTC")
    log(f"ROOT_FOLDER_ID = {ROOT_FOLDER_ID}")
    log("Approved course mappings:\n" + mappings_block)

    # --- the conversation starts with one kickoff message. ---
    kickoff = (
        f"Start translation run on folder ID {ROOT_FOLDER_ID}. "
        "Apply the routing policy from the system prompt. "
        "Use translated_log.json for dedup.\n\n"
        "## Approved course mappings (from courses.json)\n"
        f"{mappings_block}\n\n"
        "A course folder whose Hebrew name matches one of these → use the approved "
        "English name exactly. A course folder NOT in this list → auto-assign an "
        "English name, use it, persist it via update_mapping, and log the "
        "auto-naming. Do not skip a course just because it's unmapped."
    )
    messages = [
        {"role": "user", "content": kickoff}
    ]

    # --- THE LOOP — guarded by a 200 TOOL-CALL budget (not turns: one turn can
    #     dispatch many tool calls, so we count calls, not iterations). ---
    TOOL_CALL_BUDGET = 200
    tool_calls = 0

    # run-summary accumulators (structural roll-up of tool-derived events)
    saved_files = []     # (drive_filename, md_path)
    auto_named = []      # (hebrew, english)
    refusals = []        # (drive_filename, reason)
    total_cost = 0.0     # sum of cost_usd across translations this run
    tool_call_counts = Counter()   # Step 4: tool calls by name — where the turns went

    turn = 0
    budget_hit = False
    try:
        while True:
            turn += 1
            CURRENT_TURN = turn      # module global read by the translation recorder
            print(f"\n========== TURN {turn} (tool calls: {tool_calls}/{TOOL_CALL_BUDGET}) ==========")
            log(f"\n===== TURN {turn} | tool_calls={tool_calls}/{TOOL_CALL_BUDGET} =====")

            _set_cache_breakpoint(messages)
            _t0 = time.perf_counter()
            response = client.messages.create(
                model="claude-opus-4-8",
                max_tokens=4096,
                system=SYSTEM_CACHED,
                messages=messages,
                tools=TOOLS,
            )
            _routing_ms = (time.perf_counter() - _t0) * 1000

            # Cache verification: cache_read should climb turn-over-turn; if it
            # stays 0 a silent invalidator is at work (see claude-api skill).
            u = response.usage
            cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
            print(f"stop_reason: {response.stop_reason} | in/out {u.input_tokens}/{u.output_tokens} "
                  f"cache r/w {cache_read}/{cache_write}")
            log(f"stop_reason: {response.stop_reason} | usage in={u.input_tokens} out={u.output_tokens} "
                f"cache_read={cache_read} cache_write={cache_write}")

            # cost ledger: record this routing call with tiered (cache-aware) cost.
            LEDGER_ROWS.append(
                costs.record_call(LEDGER_PATH, RUN_ID, turn, "routing", response, _routing_ms)
            )

            # agent reasoning text — the audit surface. Routing rationale, type
            # inheritance, custom_subfolder choices, and skip-floor decisions all
            # live here (skips produce no tool call), so it is logged in FULL.
            for block in response.content:
                if block.type == "text":
                    print("[agent]:", block.text)
                    log("[agent reasoning]:\n" + block.text)

            # agent is finished
            if response.stop_reason == "end_turn":
                print("\n--- RUN COMPLETE ---")
                log("--- RUN COMPLETE (agent ended its turn) ---")
                break

            if response.stop_reason == "tool_use":
                # 1) record the agent's turn VERBATIM (must include the tool_use blocks)
                messages.append({"role": "assistant", "content": response.content})

                # 2) run every tool the agent asked for, collect a result per call
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue

                    # budget check BEFORE dispatching — caps the run at exactly
                    # TOOL_CALL_BUDGET real tool calls even if a turn batches many.
                    if tool_calls >= TOOL_CALL_BUDGET:
                        warn = f"TOOL CALL BUDGET EXCEEDED ({TOOL_CALL_BUDGET}) — stopping run"
                        print(warn)
                        log(warn)
                        budget_hit = True
                        break
                    tool_calls += 1
                    tool_call_counts[block.name] += 1   # Step 4: where the turns went

                    print(f"[tool call]: {block.name}({block.input})")
                    log(f"[tool call #{tool_calls}] {block.name}({log_trunc(block.input)})")

                    handler = HANDLERS[block.name]
                    result_str = handler(block.input)
                    log(f"[tool result] {log_trunc(result_str)}")

                    # --- prominent audit events derived from the tool result ---
                    # list_folder returns a JSON ARRAY, not an object; guard so only
                    # dict results (the status-carrying tools) are inspected.
                    try:
                        res = json.loads(result_str)
                    except (ValueError, TypeError):
                        res = {}
                    if not isinstance(res, dict):
                        res = {}
                    status = res.get("status")
                    if block.name == "update_mapping" and status == "mapped":
                        line = (f"AUTO-NAMED: {res['hebrew_name']} → {res['english_name']} "
                                "(agent-assigned, no prior mapping)")
                        log(line)
                        auto_named.append((res["hebrew_name"], res["english_name"]))
                    elif status == "saved":
                        fn = block.input.get("drive_filename", "?")
                        log(f"SAVED: {fn} → {res.get('md_path')}")
                        saved_files.append((fn, res.get("md_path")))
                    elif status == "refused":
                        fn = block.input.get("drive_filename", "?")
                        log(f"REFUSED: {fn} — {res.get('reason')}")
                        refusals.append((fn, res.get("reason")))
                    # accumulate spend from any result carrying cost_data (translated/refused)
                    cd = res.get("cost_data")
                    if isinstance(cd, dict) and "cost_usd" in cd:
                        total_cost += cd["cost_usd"]

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,         # ← same id, ties result to the request
                        "content": result_str,           # ← MUST be a string
                    })

                # 3) feed all results back as ONE user turn
                messages.append({"role": "user", "content": tool_results})
                if budget_hit:
                    break
                # loop continues — next create() sees the results

    except Exception as _e:
        # Transient failures (e.g. a dropped Drive socket) must not lose the
        # audit summary — record the crash and fall through to finally.
        import traceback as _tb
        crash_note = f"RUN CRASHED: {type(_e).__name__}: {_e}"
        print(crash_note)
        log(crash_note)
        log(_tb.format_exc())
    finally:
        # --- end-of-run summary block. Structural roll-up of tool-derived events;
        #     skip-floor / skipped-file decisions are reasoning-only (the autonomy
        #     reversal removed the structural skip signal), so they are captured in
        #     the per-turn reasoning above and the agent's own end-of-run report. ---
        run_end = datetime.now(timezone.utc)
        duration = (run_end - run_start).total_seconds()
        summary = ["\n===== RUN SUMMARY ====="]
        summary.append(f"files translated & saved : {len(saved_files)}")
        for fn, mp in saved_files:
            summary.append(f"   - {fn} → {mp}")
        summary.append(f"auto-named courses       : {len(auto_named)}")
        for he, en in auto_named:
            summary.append(f"   - {he} → {en}")
        summary.append(f"refusals                 : {len(refusals)}")
        for fn, reason in refusals:
            summary.append(f"   - {fn}: {reason}")
        summary.append(f"total tool calls         : {tool_calls}/{TOOL_CALL_BUDGET}"
                       + ("  (BUDGET HIT)" if budget_hit else ""))
        summary.append(f"total translation cost   : ${total_cost:.6f}")

        # --- cost-ledger roll-up: in-memory (NOT re-read from disk) so it still
        #     emits if the run crashed mid-write. Routing cost is the half the
        #     per-file manifest never sees. ---
        ledger_total = sum(r["cost_usd"] for r in LEDGER_ROWS)
        routing_cost = sum(r["cost_usd"] for r in LEDGER_ROWS if r["category"] == "routing")
        translation_cost = sum(r["cost_usd"] for r in LEDGER_ROWS
                               if r["category"].startswith("translation"))
        tok_in  = sum(r["input_tokens"] for r in LEDGER_ROWS)
        tok_cw  = sum(r["cache_creation_i_tokens"] for r in LEDGER_ROWS)
        tok_cr  = sum(r["cache_read_input_tokens"] for r in LEDGER_ROWS)
        tok_out = sum(r["output_tokens"] for r in LEDGER_ROWS)
        summary.append(f"TOTAL run cost (ledger)  : ${ledger_total:.6f}  ({len(LEDGER_ROWS)} LLM calls)")
        summary.append(f"   routing               : ${routing_cost:.6f}")
        summary.append(f"   translation           : ${translation_cost:.6f}")
        summary.append(f"tokens in/cw/cr/out      : {tok_in}/{tok_cw}/{tok_cr}/{tok_out}")
        summary.append("tool call counts         : " + (
            ", ".join(f"{n}={c}" for n, c in sorted(tool_call_counts.items())) or "(none)"))
        summary.append(f"cost ledger              : {LEDGER_PATH}")
        summary.append(f"wall-clock duration      : {duration:.1f}s")
        summary.append("skipped / skip-floor     : reasoning-only (no tool call) — "
                       "see per-turn agent reasoning above")
        summary_text = "\n".join(summary)
        print(summary_text)
        log(summary_text)
        log_f.close()
        print(f"\nFull audit log: {log_path}")
