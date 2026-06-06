# ARCHITECTURE

A state map of the system as it exists in the code today. Present tense, current
state only — reversed decisions and the path here live in `HISTORY.md`, not here.
Where the live code disagrees with what a reader would expect from comments or the
other docs, this file flags it inline rather than smoothing it over.

---

## 1. System overview

The system translates a tree of Hebrew course PDFs in Google Drive into English
Markdown notes in an Obsidian vault. A single run has two stages. First a
**deterministic pre-pass** (`prepass.py`) walks the Drive tree and diffs it
against the manifest (`translated_log.json`) by Drive's md5 checksum — metadata
only, no downloads, no LLM — producing a **worklist** of only the new or changed
files. Then a **semantic agent loop** (`agent.py`) is handed that worklist and,
for each file, calls Claude (Opus 4.8) as a tool-using agent: it reads the file,
classifies its course and type from the path and content, picks a text-vs-image
translation mode from extraction signals, translates, and saves the result to the
vault while recording it in the manifest. The deterministic stage decides *what is
new*; the agent decides *what it is and how to translate it*. If the pre-pass
finds nothing new, the agent loop never runs.

---

## 2. Execution flow, end to end

`agent.py`'s `__main__` block is the entry point. The flow:

1. **Load config.** Read `courses.json` (Hebrew→English course names) via
   `courses.load_courses()` and render it into a mappings block for the kickoff.
2. **Open run artifacts.** Create `logs/agent_<run_id>.log` (full audit trail) and
   set `LEDGER_PATH = logs/ledger_<run_id>.jsonl` (per-LLM-call cost ledger).
   `run_id` is one timestamp shared by both files so they correlate.
3. **Pre-pass.** `prepass.build_worklist(ROOT_FOLDER_ID)`:
   - `walk_tree` recurses the Drive tree via
     `drive.list_folder_children(..., include_md5=True)`, collecting every file
     with its `parent_path` and Drive `md5Checksum`. **No byte downloads.**
   - `diff_tree` (pure) compares each file against the manifest entries: a file is
     **excluded** from the worklist iff `dedup.md5_gate` fires (already translated,
     md5 unchanged) **or** `dedup.skip_unchanged` fires (deliberately skipped, md5
     unchanged). Everything else — no entry, changed md5, or a
     `not_translated_yet` placeholder with no md5 — goes on the worklist.
   - Returns `(worklist, total_scanned)`.
4. **Empty worklist ends the run.** If the worklist is empty, the loop body never
   executes (no `create()` call, no spend) and control falls straight to the
   summary.
5. **Kickoff.** The worklist (each item's path + Drive `file_id`) and the course
   mappings are formatted into one user message. The agent is told the tree is
   *already diffed* — it works the list, it does not discover or dedup.
6. **Agent loop.** Until the worklist is exhausted (`end_turn`) or the budget is
   hit: send the conversation to `client.messages.create(model="claude-opus-4-8",
   max_tokens=4096, tools=TOOLS)`, append the assistant turn, dispatch every
   requested tool call through `HANDLERS`, feed all results back as one user turn.
   Tool results carry handles, never bytes or markdown (see §5, CONTENT_CACHE).
7. **Per-file work.** For each worklist item the agent typically calls
   `read_file` → (`fetch_signal_detail` only if ambiguous) → a translate tool →
   `save_to_vault`, with `update_mapping` for an unmapped course or `skip_file`
   for a deliberate skip. The prompt mandates **save immediately** after each
   translation, before touching the next file.
8. **End-of-run cost summary.** A `finally` block always runs (even on crash or
   budget hit): files saved, courses auto-named, deliberate skips, refusals, tool
   counts, and the cost roll-up (routing vs translation, from the in-memory
   `LEDGER_ROWS`, so it survives a partial ledger write).

---

## 3. Module map

| Module | Role | Import profile |
|---|---|---|
| `agent.py` | The agent loop, the 8 tool schemas, one handler per tool, `CONTENT_CACHE`, prompt-cache breakpoints, budget guard, kickoff/worklist wiring, run logging + cost summary. | Constructs `Anthropic()` at import — **not** import-clean. |
| `prepass.py` | Deterministic pre-pass: `walk_tree` → `diff_tree` (pure md5 diff) → `build_worklist`. Naming-blind and skip-blind. | Imports `dedup`, `drive`, `manifest`. anthropic-free; needs Google libs installed (lazy auth). |
| `dedup.py` | Pure manifest dedup **decision** functions: `md5_gate`, `skip_unchanged`, `hash_dedup`. No I/O. The single source of dedup logic, shared by the pre-pass and the loop. | Only `from manifest import find_by_id`. anthropic-free, OAuth-free, import-clean. |
| `costs.py` | Cache-aware per-call cost + token ledger: `tiered_cost`, `record_call`. The single pricing source for both LLM call sites. | Stdlib only. anthropic-free, import-clean. |
| `manifest.py` | Single owner of `translated_log.json` I/O: `load_log`, `save_log` (atomic), `find_by_id`, `upsert_entry`, `sha256_of`. | Stdlib only. import-clean. |
| `drive.py` | All Google Drive logic: `download_bytes` (size-verified), `file_md5`, `list_folder_children`. Service built **lazily** on first call so `import drive` triggers no OAuth. | Google API libs at top; anthropic-free. |
| `translation_engine.py` | The two translate functions (text via pypdf, image via pdf2image vision) and `save_to_vault` (atomic .md write + manifest upsert). Loads skills as system prompts. | Imports `anthropic`, `pypdf`, `pdf2image`, `costs`, `manifest`. **not** import-clean. |
| `pdf_mode_detector.py` | Pure extraction-signal reporter (`detect_pdf_mode`). Emits raw signals, **no** text/image verdict — the agent decides. | `io`, `re`; `pypdf` imported lazily inside the function. anthropic-free, import-clean. |
| `courses.py` | Single owner of `courses.json` I/O: `load_courses`, `update_mapping` (atomic). | Stdlib only. import-clean. |
| `init_translation_log.py` | One-time **interactive** bootstrap of `translated_log.json`: walks Drive, hashes each PDF, prompts the operator to pair it with a vault `.md`, seed it `not_translated_yet`, or `skipped_permanent`. Not part of a normal run. | Google libs, `yaml`, `dotenv`; anthropic-free. |
| `skills/` | The four translation system-prompt fragments (see §11). | Markdown, not code. |

---

## 4. The tools

`agent.py` defines **8 tools** in `TOOLS`, with a matching `HANDLERS` entry each.
The module docstring, the `TOOLS` comment, and `agent_routing_prompt.md`'s tool
list all enumerate the same 8 (`list_folder`, `read_file`, `fetch_signal_detail`,
`translate_text_pdf`, `translate_image_pdf`, `save_to_vault`, `update_mapping`,
`skip_file`).

| Tool | Inputs | Output | Manifest | Role |
|---|---|---|---|---|
| `list_folder` | `folder_id` | JSON array of children `{name, id, type, mime_type}` | — | The agent's eyes on the tree; used for sibling context, not traversal. |
| `read_file` | `file_id` | `already_done` \| `ready` (scalar signals + `signals_full_handle`) \| `error` | **reads** (md5 gate + hash dedup) | Download → hash → dedup → detect. Caches bytes/md5/signals/markdown by `source_hash`. |
| `fetch_signal_detail` | `handle` | `per_page` array + `unrecognized_sample` | — | Offloaded verbose signals; called only for a genuinely ambiguous mode call. |
| `translate_text_pdf` | `source_hash`, `course`, `drive_filename`, `mode_reasoning` | `translated` (md handle + cost) \| `refused` \| `error` | — | Translate typed/pypdf-readable text via the text engine. |
| `translate_image_pdf` | `source_hash`, `course`, `drive_filename`, `mode_reasoning` | `translated` (md handle + cost) \| `refused` \| `error` | — | Translate handwritten/scanned/formula-dense PDF via vision. |
| `save_to_vault` | `course`, `source_hash`, `md_cache_handle`, `drive_file_id`, `drive_filename`, `chosen_mode`, `mode_reasoning`, +`file_type` or `custom_subfolder` | `saved` (vault-relative `md_path`) \| `error` | **writes** translated entry | Atomic `.md` write then manifest upsert. The executor for an already-made routing decision. |
| `update_mapping` | `hebrew_name`, `english_name` | `mapped` | — (writes `courses.json`) | Persist an agent-assigned course name; logged loudly as an audit line. No approval gate. |
| `skip_file` | `source_hash`, `drive_file_id`, `drive_filename`, `skip_reason` | `skipped_permanent` | **writes** `skipped_permanent` entry | Record a deliberate skip so the pre-pass drops the file for free next run. |

Bytes and translated markdown **never** appear in a tool result — only handles do
(see §5).

---

## 5. State

**`translated_log.json` (the manifest).** A JSON list of entries, one per Drive
file, keyed by `drive_file_id` (`manifest.upsert_entry` replaces in place). A
translated entry written by `save_to_vault` carries:

| Field | Meaning |
|---|---|
| `drive_file_id`, `drive_file_name` | Drive identity. |
| `source_content_hash` | `"sha256:…"` of the bytes — the dedup + cache identity. |
| `source_md5` | Drive's `md5Checksum` — powers the pre-pass freshness gate. Written only when present (absent for native Google Docs). |
| `md_path` | Vault-relative path to the `.md`. `null` for skip/seed entries. |
| `course`, `type` | Routing decision. |
| `chosen_mode`, `mode_reasoning` | `"text"`/`"image"` and the one-line signal rationale. |
| `model`, `cost_usd`, `input_tokens`, `output_tokens` | Spend. |
| `translated_at` | UTC timestamp. |
| extraction signals | The lean scalar subset from the detector (`recognizability`, `total_tokens`, `max_garbage_run_DIAGNOSTIC`, …), merged in when present. |

The `model` field doubles as a status sentinel: real model ids
(`claude-opus-4-8`, and older `claude-opus-4-7` entries) and `manual` mean
translated; `not_translated_yet` is a bootstrap seed; `skipped_permanent` is a
deliberate skip (carries `skip_reason`, `md_path: null`). The manifest currently
holds 124 entries across those five `model` values.

**`courses.json`.** Flat `{hebrew_folder_name: english_course_name}`. The agent's
spelling/consistency reference for course-folder naming only — it does **not**
gate or route. It is injected into the kickoff so the agent reads it from run
context, and the agent writes back to it via `update_mapping` when it auto-names
an unmapped course. (`drive.py`'s `ROOT_FOLDER_ID`, not `courses.json`, defines
the scanned tree.)

**`CONTENT_CACHE`** (`agent.py`). An in-memory dict living in the run's scope,
**keyed by `source_hash`** (content, not `file_id`, so a re-edited file misses the
stale entry). It holds the raw PDF bytes plus derived values under suffixed keys:
`:md5`, `:signals` (lean, manifest-bound), `:signals_full` (verbose, behind the
fetch handle), `:md` (translated markdown), `:cost`. The translate and save
handlers read these back by hash; this is how bytes/markdown/cost cross handlers
**without** ever entering the model's context. It is **not** persisted — a crash
loses it (see §12).

---

## 6. Dedup chain

A single source of truth — `dedup.py` — drives both the pre-pass and the loop:

1. **md5 gate (pre-download).** `dedup.md5_gate`: if the file already has a
   translated entry (`md_path` present) whose stored `source_md5` equals Drive's
   current md5, the bytes are provably unchanged → `already_done`, no download.
   `dedup.skip_unchanged` is the companion for `skipped_permanent` entries
   (md_path is null, so `md5_gate` can't cover them). Both are md5-only and N/A
   when Drive's md5 is `None` (native Google Docs).
2. **Cross-ID content SHA (post-download).** Inside the loop, `read_file_logic`
   downloads the bytes (size-verified by `drive.download_bytes`), hashes them, and
   calls `dedup.hash_dedup`:
   - *branch b* — by `drive_file_id`, gated on the stored `source_content_hash`
     matching: `md_path` present → `already_done`; `skipped_permanent` →
     `already_done` with the skip reason; `not_translated_yet`/other → fall
     through.
   - *branch c* — cross-ID fallback: the same content already translated under any
     other id → `already_done`. Safe only because the bytes were integrity-checked
     before hashing.
   - no hit → `PROCEED` (run the detector and translate).

The pre-pass uses only the md5 layer (downloading would defeat the point);
`hash_dedup` is the post-download authority inside the loop.

---

## 7. Mode dispatch (text vs image)

`pdf_mode_detector.detect_pdf_mode` is a **pure, verdict-free** measurement
function. It reports scalar signals (`recognizability`, `tokens_per_page`,
`bytes_per_token`, `math_token_fraction`, `max_garbage_run_DIAGNOSTIC`,
`page_count`, `file_size_kb`) plus verbose `per_page` / `unrecognized_sample`
behind a handle. The old verdict thresholds remain in the module as constants but
are **deliberately not applied** anywhere — they are reference only.

The **agent** owns the decision, per the "Mode Selection" rubric in
`agent_routing_prompt.md`: low `tokens_per_page` / high `bytes_per_token` /
high per-page variance → image; high `recognizability` + healthy yield + math
that's just fragmented LaTeX in prose → text; a formula-dense page (high
`math_token_fraction`, math dominating prose) → image even when typed.
`max_garbage_run_DIAGNOSTIC` is explicitly *not* a verdict driver. **On genuine
ambiguity, default to image** — image mode on typed content merely costs more,
whereas text mode on handwritten content silently fabricates (the Lecture 4
failure). A file is never skipped over mode ambiguity.

---

## 8. Skip model

There are two distinct "skip" concepts and they must not be conflated:

**Deliberate skip (`skip_file` → `skipped_permanent`).** The agent has *read* a
file and decided not to translate it. This is the **only** skip-by-pattern rule,
and it is **narrow**: a solution file whose name carries the **פתר** stem
(פתרון / פתור, spelling varies) nested under a homework folder carrying the
**עבוד** stem (עבודה / עבודות / עבודות בית) — i.e. a handwritten homework
answer sheet. **Both stems together are required.** The handler writes a
`skipped_permanent` manifest entry keyed by `drive_file_id` (with `source_md5`
from the read_file cache), so the next run's pre-pass drops the file via
`dedup.skip_unchanged` while its bytes are unchanged. The old broad keyword rule
(פתרון / תשובות / מענה matched anywhere) is **gone**: a פתר-stem file *outside*
the homework context is translated by default.

**Skip-floor (cannot-determine).** A file with genuinely no basis for
classification (gibberish/unreadable name, no resolving context). This is
**run-log only** — the agent logs it as unprocessed in its reasoning and the run
log, and writes **no manifest entry**. Because nothing is recorded, such a file
reappears on the next run's worklist. `skip_file` must **not** be called for
these. The pre-pass itself performs **no** skip logic of either kind — it is
skip-blind; a deliberately-skipped file stays cheap only because its bytes are
unchanged (md5), not because the pre-pass understands it.

---

## 9. Cost model

`costs.py` is the single cache-aware pricing source for both LLM call sites
(routing in `agent.py`, translation via the engine's `on_usage` callback). The
four token classes are **disjoint** in Anthropic's usage object and are each
multiplied by their own rate and summed (never subtracted), per 1e6 tokens:

| Class | Rate (USD / 1e6) |
|---|---|
| input (fresh) | `$5.00` |
| output | `$25.00` |
| cache write (5-min ephemeral) | `$6.25` (1.25× input) |
| cache read / hit | `$0.50` (0.1× input) |

`record_call` appends one OTel-aligned JSON row per call to
`logs/ledger_<run_id>.jsonl` and returns it for the in-memory roll-up; a ledger
write failure warns and still returns the row (never raises into the run). The
end-of-run summary splits **routing** vs **translation** cost — routing being the
half the per-file manifest never sees.

**Prompt caching** (routing loop only). Render order is tools → system →
messages. Two ephemeral breakpoints: one on the system block (caches tools +
system prompt together), one moved forward each turn onto the last message block
(caches the conversation prefix). `_set_cache_breakpoint` clears any prior
message-level breakpoint before setting the new one, keeping within the
4-breakpoint cap. Cache verification: `cache_read` should climb turn-over-turn.

> **Note:** `costs.py` documents the 5-minute ephemeral write rate only; a
> `ttl:"1h"` write (billed 2× input) is explicitly *not* handled. The code issues
> only 5m writes, so this is correct today — but it is a latent gap if 1h caching
> is ever adopted. There is **no 1h cache path anywhere in the live code** (the
> default 5-min TTL is the only one used).

---

## 10. Guardrails

- **Refuse rather than reconstruct.** The skills forbid fabricating unreadable
  content. A genuine refusal is signalled by `REFUSED: <reason>` as the **very
  first line** of the translation output. `_translate_logic` checks the first line
  only (so a body merely *discussing* OCR garbage can't trip it) and returns
  `status: "refused"` without caching markdown.
- **Extraction-yield backstop.** The text engine raises `RuntimeError` when pypdf
  yields <50 chars; the handler turns it into `refused` so the agent reconsiders
  image mode.
- **Save immediately.** The prompt requires `save_to_vault` right after each
  successful translation, before any other file — a mid-run failure can't strand
  unsaved work.
- **Atomic save, single writer.** `save_to_vault` writes the `.md` (temp file +
  `os.replace`) **before** touching the manifest, so a failed disk write never
  leaves a manifest record for an absent file. All manifest I/O goes through
  `manifest.py` (the sole writer); `save_log` is itself atomic.
- **Download integrity.** `drive.download_bytes` verifies length against Drive's
  reported `size` and re-downloads on a short read, so truncated bytes are never
  hashed or translated.
- **Tool-call budget.** The loop is capped at **200 real tool calls**
  (`TOOL_CALL_BUDGET`), checked before each dispatch (not per turn, since one turn
  can batch many calls).
- **Crash-safe summary.** The loop is wrapped in try/except/finally so a transient
  failure still emits the audit summary and closes the log.

---

## 11. Skills

`translation_engine.py` builds each translate tool's system prompt by
concatenating `skills/translate-shared.md` (the shared craft: output format, YAML
frontmatter, figure rules, glossary, the refuse-rather-than-reconstruct contract)
with the mode-specific fragment — `skills/translate-text-pdf.md` (reassemble
fragmented typed math into LaTeX; don't refuse over fragmentation) or
`skills/translate-image-pdf.md` (vision input; the figures *are* visible). Skills
are loaded **per call** (per-invocation) by `_text_system_prompt()` /
`_image_system_prompt()`, not once at import — the locked design decision
(HISTORY "Day 2 redesign"), so editing a skill takes effect on the next
translation without restarting the process. `skills/save-to-vault.md` documents
the executor contract (no re-deciding routing; atomic write; in-place upsert). The
agent loop's own system prompt is the separate `agent_routing_prompt.md`.

---

## 12. Known limits / deferred

- **Cache is in-memory only.** `CONTENT_CACHE` is not persisted; a crash between
  translate and save loses the translated markdown. The "save immediately" rule
  narrows but does not close this window — a disk-persisted cache (a hard save
  guarantee) is deferred.
- **No `--audit` mode.** A standalone manifest-audit mode (re-verify hashes,
  detect stale/orphaned entries) is not implemented. The audit *logic* was run
  once manually as the md5 backfill.
- **No hybrid PDF mode.** A file is translated wholly in text **or** image mode; a
  per-page hybrid path does not exist (the skills describe "hybrid" as a
  translator judgment within one mode, not a separate engine).
- **The 404 orphan.** STATUS/PHASE2_NOTES/HISTORY record **one** manifest entry
  whose Drive source has 404'd, found during the md5 backfill and left in place as
  harmless. It carries **no** structural marker (no `404`/`orphan`/`missing`
  field), so it is indistinguishable from a normal translated entry without a live
  Drive call. **This claim is unverifiable from the repo alone** — the code and
  manifest contain nothing that identifies it.

> **Flag — disagreement with the task brief / older docs.** The brief lists "2
> bootstrap `skipped_permanent` entries missing `source_md5`." That is **no longer
> true**: the live manifest has **0** entries missing `source_md5` (all 11
> `skipped_permanent` entries carry it), backfilled by commit `5e87909`
> ("manifest updates from pre-pass runs + Ass1 md5 backfill"). Every
> `skipped_permanent` entry is therefore pre-pass-skippable today. The file/entry
> counts in `STATUS.md` (98 files / 104 entries) also predate the recent pre-pass
> runs — the manifest now holds 124 entries.
