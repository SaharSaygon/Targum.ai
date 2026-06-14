# STATUS

> **Project name (2026-06-14):** brand + GitHub repo = **`Targum.ai`** (with the dot);
> local clone directory / filesystem = **`Targum_ai`** (dotless — illegal as a Python
> identifier with the dot). No code/module names changed by the rename. See HISTORY
> "Project rename: → Targum.ai / Targum_ai (2026-06-14)". Manual follow-ups (rename the
> GitHub repo, `git remote set-url`, rename the local dir) are the user's to do.

## Current state
**Phase 1 COMPLETE.** The full agent loop is built and running end-to-end on real
Drive folders: autonomous on naming + routing (no approval gate), budget-guarded
(200 tool-call cap), audit-logged (loud decision log), and crash-resilient
(crash-safe end-of-run summary). **8 tools** (was 7): the 6 core handlers
(`list_folder`, `read_file`, `translate_text_pdf`, `translate_image_pdf`,
`save_to_vault`, `update_mapping`), `fetch_signal_detail` for offloaded-signal
retrieval, and `skip_file` (records a deliberate skip as `skipped_permanent` — added
with the pre-pass, 2026-06-06).

Semester ד is fully translated. The manifest now holds **124 entries** (incl. the one 404 orphan), of which **11 are `skipped_permanent`** — and **all 11 now carry `source_md5`** (the 2 md5-less bootstrap entries were backfilled, commit `5e87909`), so the pre-pass drops every skip for free on every later run.

**Prompt caching VERIFIED live**: `cache_read` climbs turn over turn; steady-state
cost is ~2 input tokens per turn once the cache is warm.

**Optimizations landed:**
- **Save-immediately** rule — translate → save in the same step (soft side of Gap 3);
  shrinks the window where a translated-but-unsaved file is lost to a crash.
- **Context-offloading** — verbose detector signals (`per_page`,
  `unrecognized_sample`) live behind handles; the agent retrieves them on demand
  via `fetch_signal_detail` instead of carrying them in message history.
- **md5 freshness gate + backfill** — skip the byte download when Drive's
  `md5Checksum` matches the stored `source_md5`. Coverage is now complete, incl. all **11/11** `skipped_permanent` entries (the last 2 bootstrap entries backfilled, commit `5e87909`).
- **Cross-ID SHA dedup**, **integrity-checked downloads**, in-memory
  `CONTENT_CACHE@source_hash`.

**Known harmless artifact:** one dangling manifest entry whose source 404'd (file
removed/moved in Drive). Left in place — it costs nothing; not worth a manifest edit.

**Optimization pass done (2026-06-06).** Acted on the `ARCHITECTURE.md`/`OPTIMIZATION.md`
scan — code-only, no behavior change except a cost-ledger bug fix. Removed dead code and the
two divergent manual scripts (`translate_one.py` / `translate_image_pdf.py`, with the
`infer_type` / `_TYPE_KEYWORDS` / engine `sha256_of` cascade); deleted `list_drive.py` /
`hello_world.py`; dropped the ignored `save_to_vault` `filename` field. **New modules:**
`dedup.py` (pure, anthropic-free dedup verdicts extracted from `read_file_logic`; `test_dedup.py`
alongside) and `costs.py` (cache-aware cost ledger). **`drive.py` now lazy-inits** the service
via `get_service()` — `import drive` is credential-free. **Cost ledger live:** per-call
`logs/ledger_<run_id>.jsonl`, tiered pricing ($5 in / $6.25 cache-write / $0.50 cache-read /
$25 out), end-of-run summary reports true total + routing-vs-translation split. Fixed a real
~10× overstatement of routing cost (old code billed cache-read tokens at full input price).
`fetch_signal_detail` was KEPT (not dropped) as a fallback for ambiguous files, with a
re-eval note.

**Deterministic routing pre-pass — COMMITTED and run live (2026-06-06).** Module `prepass.py`
(pure, no `anthropic` import — import-clean like `dedup.py` / `costs.py`): recursively walks the
Drive tree via `list_folder_children` (md5 metadata only, no byte downloads), diffs each file
against the manifest via `dedup.py`, emits a worklist of only new/changed files. Unchanged files
(including already-marked skips) are **absent — absence is the signal**. The agent loop and tools
are unchanged; the kickoff hands the loop the worklist instead of a root folder ID (`while True`
→ `while worklist`); **empty worklist → end run, no spend**. Supporting:
`drive.list_folder_children` gained `include_md5=False` (agent's `list_folder` unchanged at
default), `dedup.skip_unchanged` added, `test_prepass.py` (7 tests), `test_dedup.py` still 16/16.

**First committed worklist run (`agent_20260606_203523`).** Pre-pass scanned **121 files → 19
worklist** (102 unchanged excluded, no byte reads). The loop: **8 translated + saved** (4 clean
typed homework solutions text-mode + 4 Linear-Systems tutorials), **10 deliberate skips** via
`skip_file` (handwritten פתר solutions under עבוד homework, incl. 3 Algo answer sheets), **1
`already_done`** (`Algo262_Ass1_AnswerSheet`, matched a bootstrap entry by content hash via
in-loop `hash_dedup`). Tool calls **45/200** (read_file 19, skip_file 10, translate_text_pdf 8,
save_to_vault 8, 0 auto-named). Cost **$3.40** (routing **$0.66** / translation **$2.73**, 33 LLM
calls), **~18 min** wall-clock, **0 refusals, 0 errors**. The 19 reads = 8 translated + 10 skipped
+ 1 already-done.

## Next step
Phase 2 entry. (Immediate housekeeping: commit the remaining doc updates from this session.)

The **DATA gate is resolved** — and not by confirming the number. The "routing ≈ 88%" premise
turned out **run-composition-dependent**: an n=1 artifact of a 1-new-file run; the 2026-06-06
verification run (7 new files) split routing/translation ~50/50. Not a stable metric, so the
justification is reframed to **absolute routing-$ on a near-static tree** (~$2/run, almost all
re-derivation over unchanged files) — which the pre-pass eliminates by diffing deterministically.
See HISTORY "Deterministic routing pre-pass built + skip-rule narrowed (2026-06-06)".

**Coupled change — skip rule narrowed (REVERSAL).** The broad SKIP-keyword rule
(פתור/פתרון/תשובות/מענה → skip any matching segment) is **gone**. There is **no skip rule in the
pre-pass** and no broad keyword rule. Skip is the agent's decision on **new files only**, and there
are now **two deliberate skip cases — both the user's OWN solutions:**
- **Homework solutions (signal-decided):** a solution segment (stem פתר) under a homework folder
  (stem עבוד) that reads **handwritten** (low tokens/page, huge bytes/token, low recognizability)
  → `skipped_permanent` via `skip_file`. Typed/official homework solutions translate.
- **Exam solutions (ownership-decided, added 2026-06-14):** a פתר-solution under a מבחן/בוחן (or
  מועד א/ב/ג) exam ancestor that is **the user's own** → skip. Ownership is decided two ways,
  either one triggers the skip: an explicit "my solutions" marker in the path (`פתרונות שלי` /
  bare `שלי` / Latin "my" — catches a typed exam form filled in by hand), OR handwritten signals.
  **We do NOT translate the user's own exam solutions — only official typed ones** (e.g.
  "Moed A - Solution", a `פתרונות רשמיים` folder) translate (type: exam).

Solution-like names outside BOTH the homework- and exam-solution contexts translate normally; the
pre-pass drops every recorded skip on later runs. (The earlier "2 bootstrap `skipped_permanent`
entries lack `source_md5` → re-enter once" caveat is **RESOLVED** — both were backfilled, commit
`5e87909`; all 11 such entries are md5-backed and drop for free.)

**Latest run — rename verification (`agent_20260614_202844`, 2026-06-14).** After the project
rename, ran the agent end-to-end to confirm nothing broke. Pre-pass **scanned 213 → 1 worklist**;
outcome **0 translated, 1 skip** (a handwritten homework solution, `עבודה 6.pdf`). **$0.092** routing
only, **37.4s**, **0 refusals / 0 errors**. Auth, imports, loop, and routing all intact — the rename
was docs-only. See HISTORY "Rename-verification live run".

## Deferred (not blocking)
- Disk-persisted cache as the HARD save-after-translate guarantee (the soft
  save-immediately rule is the current mitigation — see PHASE2_NOTES #3b).
- Standing manifest-integrity `--audit` mode (the md5 backfill was a one-off
  manual run of this logic — PHASE2_NOTES #6).
