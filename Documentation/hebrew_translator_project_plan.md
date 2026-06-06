# Hebrew Lecture Translator Agent — Complete Project Plan

## Project Overview

An AI agent that scans a Google Drive folder for Hebrew lecture materials, translates them to English Markdown files, and saves them to an Obsidian vault. Built as a learning project to understand agent architecture, Python, and the Anthropic API.

---

## Locked-In Design Decisions

> **Reconciled 2026-06-04 against the built code (Phase 1 complete).** Several
> decisions below were REVERSED during the build-out; reversals are marked
> ~~struck through~~ with the replacement and a pointer to HISTORY.md, not
> silently rewritten — the reversal is part of the design story.

### Architecture
- **Agent type**: Python script using Anthropic API with tool-use loop (not a fixed script)
- **Development environment**: Claude Code for aggressive pair-programming
- **Editor**: VS Code (Mac, MacBook Air M5)
- **Language learning**: Python absorbed through building (not studied separately)

### Storage & Sync
- **Source**: Google Drive "iPad notebook" folder (read-only access)
- **Destination**: Obsidian vault only (no write-back to Drive)
- **Agent code**: Private GitHub repo (`Agent_Translator_English_MD`)
- **Obsidian vault**: Separate private GitHub repo (for Phase 3 cloud sync)
- **Folder structure**: Mirror Drive's organization, translated to English
- **Vault path**: `/Users/saharsaygon/Documents/Obsidian Vault/` (Mac, current)

### State & Mapping Files
- **`courses.json`**: Hebrew course name → English course name. Supplies the approved English SPELLING (consistency across ~60 files/semester — Obsidian fragments on casing variants), not a routing table. ~~Agent never writes to it without explicit user approval. Unknown course names block (Phase 1) or skip+log (Phase 3).~~ — **REVERSED 2026-06-02 (full autonomy, see HISTORY "Day 2 Part 4")**: the agent auto-names unknown courses and persists them via `update_mapping` **in-loop, no approval guard**; only genuine cannot-determine cases skip+log (the honesty floor). Injected into the kickoff message (Decision A), not read as a tool.
- **`translated_log.json`** *(NEW)*: state manifest tracking every file the agent has seen. Three jobs:
  1. **Inventory** — Drive ID, filename, SHA-256 hash of source bytes for every file
  2. **Hash-based dedup** — better than checking ".md exists"; catches re-edits of source PDFs
  3. **Skip persistence** — `skipped_permanent` files with `skip_reason` are remembered across runs
  
  Schema per entry:
  ```
  drive_file_id, drive_file_name, source_content_hash,
  md_path (relative to vault root), course, type,
  translated_at, model, skip_reason (optional),
  cost_usd, input_tokens, output_tokens
  ```
  
  `model` values: `manual` (translated outside pipeline), `not_translated_yet`, `skipped_permanent`, `claude-opus-4-8` (agent translations with cost data — canonical full string, not the short `opus-4-8`). Entries also carry `source_md5` for the md5 freshness gate (see "md5 freshness gate" below).
  
  This file IS committed to repo — it's state the agent needs across machines, not a transient log.

- **No `subfolder_types.json`** — folder/file role is determined semantically by the agent reading Hebrew names and applying the routing policy in the system prompt. Lookup tables proved fragile against real-world naming variation.

### Traversal & Routing — Delegated to Agent Reasoning
- **Decision**: No hardcoded traversal logic. `claude-opus-4-8` decides what to translate and where to put it, given the routing policy in `agent_routing_prompt.md` and a small tool set.
- **Rationale**: This is the point of building an agent rather than a script. Hardcoded rules can't anticipate messy real-world folder structures; Claude can. Less code, more adaptable across semesters and across course conventions.
- **Tools** (built — 6 core + 1): `list_folder`, `read_file`, `translate_text_pdf`, `translate_image_pdf`, `save_to_vault`, `update_mapping` + `fetch_signal_detail` (retrieves offloaded verbose detector signals on demand). ~~`ask_user`~~ — **REMOVED 2026-06-02 (full autonomy, see HISTORY "Day 2 Part 4")**: no approval/interaction gate; routing + naming uncertainty is handled by the agent itself, with genuine cannot-determine cases skipped + logged. (`flag_for_approval`, briefly considered, was also removed.) Tool design = real architecture work; the system prompt = the policy spec. Text and image translation are separate tools — mode dispatch IS the tool call (see "Mode Dispatch — Agent-Driven via Signals" below).
- **Drive structure (typical, not enforced)**: Course folder contains Lectures/Tutorials as flat files at top level. Exams and Homework live in container folders, often split into `נקי` (clean → translate) and `פתור` (solved → skip).
- **Semantic role classification (in system prompt)**:
  - LECTURES: הרצאה, שיעור (when not שיעורי בית), פרק, נושא + morphological variants
  - TUTORIALS: תרגול, תרגיל (standalone), כיתה
  - HOMEWORK: שיעורי בית, עבודה, מטלה, תרגילי בית, ש"ב
  - EXAMS: מבחן, בוחן, מועד א/ב/ג
  - SKIP: פתור, פתרון, תשובות, מענה
  - CLEAN: נקי, ריק, ללא פתרון, שאלות (inherits type from parent container)
- **Rules**:
  - Skip wins — any segment of the path matching SKIP → don't translate
  - Inherit type when filename alone isn't classifying (e.g., "2023.pdf" inside מבחנים/נקי/ → exam)
  - **Dedup via `translated_log.json` hash lookup** — if source bytes hash matches an entry with non-null `md_path` → skip; if hash matches `skipped_permanent` entry → skip; otherwise translate. Dedup is **cross-ID** (keyed on source content SHA across Drive file IDs — same bytes under a new ID still skip). A **md5 freshness gate** runs first: if Drive's `md5Checksum` matches stored `source_md5`, skip the byte download entirely. The dedup *decision* logic (md5 gate + hash / cross-ID-SHA verdicts) is factored into **`dedup.py`** — a pure, anthropic-free helper extracted from `read_file_logic`; single source of dedup truth and a prerequisite for the routing pre-pass (see HISTORY "Optimization pass (2026-06-06)").
  - ~~Genuine uncertainty → call `ask_user`, never silent guessing~~ — **REVERSED (no `ask_user`)**: the agent decides autonomously; genuine cannot-determine cases are **skipped + logged** (honesty floor), never silently guessed.
- **Cost guard**: hard cap on tool calls per run (e.g., 200) to prevent runaway loops burning API credits.
- **Logging**: every routing/skip decision logged with the agent's reasoning, otherwise debugging becomes guesswork.
- **Cost ledger**: `costs.py` — cache-aware per-call cost ledger (tiered pricing; OTel-aligned JSONL rows in `logs/ledger_<run_id>.jsonl`; end-of-run total + routing-vs-translation split). See HISTORY "Optimization pass (2026-06-06)".

### Translation Engine
- **System prompt**: Adapted from "Translating Hebrew Lectures" Claude Project. Routing policy lives in `agent_routing_prompt.md`; execution craft is split into shared + thin mode-specific skills (see Skills below). Manually synced when instructions change.
- **Model**: **`claude-opus-4-8` for all agent translation work** (lectures, tutorials, homework, exams). No Sonnet, no Haiku. ~~`claude-opus-4-8`~~ — migrated to `claude-opus-4-8` (see HISTORY "Model migration").
- **Pricing (corrected)**: **$5 / M input, $25 / M output**. The earlier $15/$75 figures were ~3× inflated; the corrected rate drops the semester projection from ~$40 to **~$13**. **Prompt caching is enabled and verified live** — steady-state input settles to ~2 tokens/turn once the cache is warm (default 5-min TTL; `ttl:"1h"` only for big/image-heavy sweeps).
- **Token limits**: `max_tokens=16000` per translation call. First real lecture used 7,135 output tokens; 16K gives 2× headroom for dense 10–15 page lectures.

### Mode Dispatch — Agent-Driven via Signals
- **`pdf_mode_detector` returns SIGNALS ONLY, no verdict**: recognizability, max_garbage_run, math-italic counts, preview text, unrecognized_sample, page_count, file_size_kb. No `mode` field, no threshold gates — the agent reads the signals and picks the mode itself.
- **Context-offloading**: the verbose signals (`per_page` arrays, `unrecognized_sample`) are kept OUT of message history behind handles and retrieved on demand via `fetch_signal_detail`. In practice this tool got **0 calls** — the summary signals carried the mode decision; the verbose detail was not load-bearing for any Phase 1 file. ~~(Open question: drop `fetch_signal_detail`.)~~ — **RESOLVED 2026-06-06: KEEP** (see HISTORY "Optimization pass"). 0 calls means the scalars sufficed *so far*, not that a future genuinely-ambiguous file won't need the per-page detail; it's cheap to keep behind its handle and costly to rebuild later. Retained with a re-evaluation note at its definition.
- **Two translation tools** (`translate_text_pdf`, `translate_image_pdf`) instead of one dispatcher with a mode param. The agent picks based on signals from `read_file`.
- **Agent reads signals and decides**: fragmented Mathematical-Italic tokens mixed with parens/digits/operators → text mode (LaTeX rendering artifact, not OCR garbage); whitespace/punctuation/single-char noise → image mode (handwriting signature); mixed/ambiguous → image mode.
- **Default-to-image on ambiguity.** Image is the safer failure direction — text-mode on handwritten silently fabricates; image-mode on typed just costs more. (There is no `ask_user` fallback — removed in the autonomy reversal; routing/naming uncertainty is resolved autonomously, with true cannot-determine cases skipped + logged.)
- **Per-tool execution skills** at `<repo>/skills/<name>/SKILL.md`, **loaded per-call** by the tool implementation. Skills encode execution craft (DPI, prompt overrides, vault path map, blank-box handling) so the agent loop stays focused on routing and policy.
- **Manifest fields** for evaluation: `chosen_mode` and `mode_reasoning` (one-line) on every translation entry.
- **Background**: replaces the threshold-based dispatcher (0.85 recognizability / 20-token garbage run) that misclassified formula-heavy typed PDFs as image-mode. See HISTORY.md "Architectural pivot" for the full rationale.

### Skills
Per-tool execution craft, loaded by the tool at invocation time. Located at `<repo>/skills/<skill-name>/SKILL.md`. **Architecture: one SHARED skill + thin mode-specific skills** — common translation craft (refuse-rather-than-reconstruct, the structural `REFUSED:` first-line marker, date injection, output format) lives once in `translate-shared.md`; each mode skill carries only what differs (DPI/vision for image, LaTeX/pypdf handling for text). Chosen for DRY / anti-drift. The skills:
- **`translate-text-pdf`** — LaTeX preservation when pypdf fragments math, code-level date injection, prompt emphasis ("fragmented math is not OCR garbage — translate faithfully, don't reconstruct"), `max_tokens=16000`, `claude-opus-4-8`, cost capture from `response.usage`.
- **`translate-image-pdf`** — pdf2image @ 200 DPI (no higher), base64 vision content blocks one per page in order, vision preamble override, code-level date injection, blank-box handling rule (leave empty fields blank, mark `[blank in source]`), refuse-rather-than-reconstruct for unreadable handwriting, `max_tokens=16000`, `claude-opus-4-8`, cost capture.
- **`save-to-vault`** — vault-relative path construction (`<vault>/<english_course>/<type>/<filename>_EN.md`), type→subfolder map (lecture→Lectures, tutorial→Tutorials, homework→Homework, exam→Exams), filename convention (preserve source stem + `_EN.md`), atomic write (md first, manifest only if md succeeded), manifest update by `drive_file_id` in place, UTF-8 explicit on every open, paths always vault-relative.

### PDF Handling
- **Text PDFs** → extract text with pypdf, send text to Claude (cheap, fast)
- **Handwritten/scanned PDFs** → convert pages to images with pdf2image, send to Claude vision (necessary for handwriting)
- **Detection**: agent reads signals from `read_file` (see Mode Dispatch — Agent-Driven via Signals above) and picks the appropriate translation tool.
- **Known weakness**: even on text PDFs, graph/diagram figures lose structure. Phase 2 idea: hybrid mode — text extraction for body, vision for figure-heavy pages.

### Output Format
- **Response begins directly with YAML frontmatter** — no preamble, no acknowledgment
- **Frontmatter fields**: `course`, `source_type` (lecture/tutorial/homework/exam), `lecture_number`, `lecture_date`, `source_file`, `date_translated` (today's actual date — omit if unknown, never fabricate), `topics`
- **Filename wins over document header** on metadata conflicts (lecture number, date) — record filename's value, flag discrepancy in Translator Notes
- **Math notation**: LaTeX delimiters (`$...$` and `$$...$$`) for Obsidian rendering; `$$\boxed{...}$$` for highlighted key equations
- **Ambiguous terms**: Hebrew kept in parentheses on first major use, English-only afterward
- **No intermediate output** — final version only, no "let me redo this" passes

### Organization in Obsidian
- Per-course folders: `Lectures/`, `Tutorials/`, `Homework/`, `Exams/`
- File type detected semantically from Hebrew filename + parent folder context
- Exam tags: `#exam`, `#course/<name>`, year metadata when identifiable
- **Paths in `translated_log.json` always relative to vault root** — no absolute paths (cross-machine + cloud compatibility)

### Scheduling & Execution
- **Phase 1-2**: Manual trigger (`python agent.py`)
- **Phase 3**: GitHub Actions cron, every 2 days, runs in cloud (laptop state irrelevant). Cloud agent reads + commits updates to `translated_log.json`.
- **On-demand**: CLI argument `--folder "path"` for specific folder runs

### Notifications
- **Method**: Email via Resend or Gmail SMTP
- **Content**: Summary of translated files + any failures
- **Trigger**: End of every run

### Dependency Management
- **Now**: `requirements.txt`
- **Later**: upgrade to `uv`

### Paths
- **All paths use `pathlib`** (cross-platform)
- **No hardcoded paths in code** — use `.env` or config file
- **All paths in state files (`translated_log.json`) are relative to vault root**

### Security — NEVER COMMIT TO GIT
- `.env` (API keys)
- `credentials.json` (Google OAuth)
- `token.json` (Google access tokens)
- `__pycache__/`
- `.venv/`
- `*.log`
- `.DS_Store`
- **Always `git status` before every push. Rotate any leaked key immediately.**

### Cost Note
**Corrected pricing ($5/M in, $25/M out) + prompt caching.** Real per-file cost data at
corrected pricing: text **$0.302** (14559 in / 9172 out), image **$0.338** (22511 in /
9004 out). Per-semester projection now **~$13** (was ~$40 at the inflated $15/$75).
Caching collapses re-walk/routing cost — steady-state ~2 input tokens/turn once warm.
**Cost lesson (the $60 post-mortem, see HISTORY):** the *routing* half of cost
(tree-walking, folder reads, decisions) was uninstrumented and accumulated invisibly
across uncached re-runs (~$39 routing vs ~$21 translation across 5 re-runs). Fixed by
caching + per-turn `cache_read`/`cache_write` logging. The 200 tool-call budget caps a
runaway walk.

**Cost mechanism corrected — tiered + cache-aware (2026-06-06, see HISTORY "Optimization
pass").** ~~Cost = input×$5 + output×$25 (flat).~~ — that flat form **mis-billed cached
runs**: `cache_read` tokens were charged at the full input rate, a ~10× overstatement on the
cache-heavy routing half. Cost is now computed by `costs.tiered_cost` over the four *disjoint*
token classes: **$5 input / $6.25 cache-write / $0.50 cache-read / $25 output** per M.
`_calc_cost` delegates to it (translation calls carry no cache tokens, so their figure is
unchanged; only the cached routing call moves). A per-call ledger
(`logs/ledger_<run_id>.jsonl`) now records every `messages.create`, and the end-of-run summary
reports true total cost with the routing-vs-translation split.

---

## Phase 1 — Lazy Working Version

**Goal**: End-to-end translation pipeline working for real lectures. Manual trigger only. Single folder. Minimal features.

| Step | Task | Hours | Status |
|------|------|-------|--------|
| 1.1 | Install Python, VS Code, Git, Claude Code (Win + Mac) | 0.5 | ✅ |
| 1.2 | Create GitHub account + private `Agent_Translator_English_MD` repo | 0.5 | ✅ |
| 1.3 | Initialize local project folder, link to GitHub, configure `.gitignore` | 0.5 | ✅ |
| 1.4 | Get Anthropic API key, store in `.env`, verify with "hello world" API call | 1 | ✅ |
| 1.5 | Google Cloud Console setup: enable Drive API, create OAuth credentials, download `credentials.json` | 1.5 | ✅ |
| 1.6 | First Drive script: authenticate and list files in iPad notebook folder | 1 | ✅ |
| 1.7 | Read file content from Drive (text files first) | 1 | ✅ |
| 1.8 | Write translation function: send text to Claude `claude-opus-4-8` with system prompt | 1.5 | ✅ |
| 1.9 | Save translated `.md` to local Obsidian vault path | 0.5 | ✅ |
| 1.10 | ~~Detect already-translated by .md existence~~ → **Replaced**: hash-based dedup via `translated_log.json` lookup. Already implemented during bootstrap. | 0.5 | ✅ |
| 1.11 | ~~Subfolder recursion~~ → Delegated to agent reasoning (see 1.18) | 0 | n/a |
| 1.12 | Create `courses.json`. (`subfolder_types.json` eliminated — semantic routing instead.) | 0.5 | ✅ |
| 1.12b | *(NEW)* Create `translated_log.json` state manifest + `bootstrap_log.py` to seed it from existing translated corpus | 1 | ✅ |
| 1.13 | Basic YAML frontmatter generation (course, type, source_file, etc.) | 1 | ✅ |
| 1.14 | Text PDF support: extract text with pypdf | 1 | ✅ |
| 1.14b | *(NEW)* Normalize `translated_log.json` paths to vault-relative (cleanup) | 0.5 | ✅ |
| 1.15 | Image PDF support: convert pages to images with `pdf2image`, send to Claude vision | 2 | ✅ |
| 1.16 | Automatic detection: text vs image PDF (OCR artifact heuristic) [^pivot] | 1 | ✅ |
| 1.17 | Console summary at end of run (what was translated, costs, durations) — crash-safe | 0.5 | ✅ |
| 1.17b | *(NEW)* Refactor — signals-only detector (strip verdict), delete `translate_smart.py`, create `skills/` (shared + thin mode skills), add Mode Selection rubric to `agent_routing_prompt.md`, backfill `chosen_mode` + `mode_reasoning` in `translated_log.json` | 2.5 | ✅ |
| 1.18 | Agent loop: tool definitions (6 core: `list_folder`, `read_file`, `translate_text_pdf`, `translate_image_pdf`, `save_to_vault`, `update_mapping`; + `fetch_signal_detail`) + routing policy + 200 tool-call budget cap + loud decision logging + `translated_log.json` integration. `ask_user`/`flag_for_approval` REMOVED (autonomy reversal); execution craft in `skills/`. | 3 | ✅ |
| 1.19 | Prompt revision pass — apply batched issues from real-translation observations | 0.5 | ✅ |
| 1.20 | Refactor pass with Claude Code (clean up structure before Phase 2) | 1 | ✅ |
| 1.21 | First real end-to-end agent run on actual course folder (Semester ד, ~97 files) | 0.5 | ✅ |

[^pivot]: Heuristic implemented and calibrated; later refactored to return signals only — see HISTORY.md architectural pivot.

**Phase 1 Total: ~21 hours** — ✅ **COMPLETE** (all steps done; Semester ד fully translated, ~97 files, caching verified live)

---

## Phase 2 — Proper Version

**Goal**: Make the agent robust, feature-complete, and ready for unattended operation.

> **Detailed rationale, current status, and reconciliation of every optimization item
> is in `PHASE2_NOTES.md`** (repo root, alongside STATUS/HISTORY). The table below is
> the forward plan only — do not duplicate the notes here. Several items were partly or
> fully delivered during the Phase 1 build-out (caching, download integrity, cross-ID
> dedup, md5 gate, save-immediately, partial routing-cost telemetry); see PHASE2_NOTES
> for what's Done vs Open vs Deferred.

| Step | Task | Hours | Note |
|------|------|-------|------|
| 2.1 | Command-line arguments with `argparse`: `--folder "path"`, `--dry-run`, `--force` | 1 | `--folder` scoping already used as the manual resume strategy (PHASE2_NOTES #2) |
| 2.2 | ~~Polish `ask_user`~~ + `update_mapping` tooling: edge cases, decision-log shape | 1.5 | `ask_user` REMOVED (autonomy reversal); `update_mapping` is in-loop, no gate |
| 2.3 | Hybrid PDF mode: text extraction for body, vision for figure-heavy pages | 2 | — |
| 2.4 | Separate Obsidian folder structure: `Lectures/`, `Tutorials/`, `Homework/`, `Exams/` | 1 | — |
| 2.5 | Enhanced frontmatter: date extraction, year metadata for exams, tags | 1 | — |
| 2.6 | Robust error handling: one failed file doesn't crash the run; new `translated_log.json` states (`skipped_transient`, `error_message`) | 1.5 | **partially done** — loop never crashes on a file (errors return as strings); truncation integrity-check + download integrity already landed (PHASE2_NOTES #4) |
| 2.7 | File logging (not just console): `logs/YYYY-MM-DD.log` | 1 | — |
| 2.8 | Email notification setup: Resend account, API key, integration | 1.5 | — |
| 2.9 | Email summary content: translated list, failures, duration, costs | 1 | needs the end-of-run cost total (PHASE2_NOTES #8) |
| 2.10 | Retry logic for transient API failures | 1 | **partially done** — SDK `num_retries` covers transport errors; explicit length-verify loop added for truncation (PHASE2_NOTES note in HISTORY) |
| 2.11 | Refactor pass with Claude Code (clean up before Phase 3) | 1 | — |

**Phase 2 Total: 13.5 hours** — not started as a phase; several items pre-delivered (see PHASE2_NOTES.md)

---

## Phase 3 — Automation

**Goal**: True always-on scheduling. Runs every 2 days even when laptop is closed.

| Step | Task | Hours |
|------|------|-------|
| 3.1 | Create separate Obsidian vault GitHub repo | 0.5 |
| 3.2 | Install Obsidian Git plugin on local machine, configure auto-pull | 0.5 |
| 3.3 | Store secrets in GitHub Actions: `ANTHROPIC_API_KEY`, `GOOGLE_CREDENTIALS`, `RESEND_API_KEY`, `EMAIL_TO` | 1 |
| 3.4 | Write `.github/workflows/translate.yml` with cron schedule (every 2 days) | 1 |
| 3.5 | Adapt agent to work in cloud environment (headless, no interactive prompts — `ask_user` becomes "skip + log") | 1.5 |
| 3.6 | Agent commits translated files to Obsidian vault repo AND commits updated `translated_log.json` back to agent repo | 1.5 |
| 3.7 | Handle unknown folders in cloud: skip + log for later approval | 0.5 |
| 3.8 | Test manual GitHub Actions trigger to verify setup | 0.5 |
| 3.9 | Monitor first scheduled run, debug any cloud-specific issues | 1 |
| 3.10 | Final documentation: README with setup instructions for future-you | 0.5 |

**Phase 3 Total: 8.5 hours**

---

## Grand Total

| Phase | Hours | Status |
|-------|-------|--------|
| Phase 1 | 21 | ✅ COMPLETE |
| Phase 2 | 13.5 | not started (several items pre-delivered — see PHASE2_NOTES.md) |
| Phase 3 | 8.5 | not started |
| **Total** | **43** | |

**Calendar time**: 3-6 weeks of evening/weekend work.

---

## Reminders

- ⚠️ **Never commit `.env`, `credentials.json`, `token.json`** — `git status` before every push
- Refactor between each phase
- Ship lazy versions first
- Learn Python by building, not studying
- Stuck >30 min → ask Claude Code
- `claude-opus-4-8` for all translations — quality over cost
- Translate 2–3 files between prompt revisions, not one — patterns emerge from data, not single observations
- All paths in state files are relative to vault root — no absolute paths
- `translated_log.json` is state, not a log; commit it, treat it as source of truth for "what's been done"
- Skills encode per-tool execution craft; agent loop encodes routing and policy. Don't conflate.
