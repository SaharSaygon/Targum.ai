# PHASE 2 NOTES — optimization backlog

Relocated to repo root (alongside STATUS.md / HISTORY.md / the project plan) and
reconciled **2026-06-04** against what the Phase 1 build-out actually delivered.
Status legend: **Done-verified** / **Done** / **Partial** / **Open** / **Deferred** /
**Known limit — not fixing**.

---

### 1. Prompt caching — **Done-verified**
Enabled and confirmed live: `cache_read` token counts climb turn over turn; steady-state
input settles to ~2 tokens/turn once warm. TTL: default is **5 minutes** (expires mid-run
on slow image-heavy sweeps) — use `ttl:"1h"` ONLY for big/image-heavy sweeps; default is
fine for normal text runs. This is the single biggest cost lever (drove the routing-cost
collapse in the $60 post-mortem).

### 2. Traversal checkpoint — **Deferred (likely permanent)**
Was: persist a mid-walk checkpoint so a crashed run resumes where it stopped.
Why deferred: caching dropped routing cost ~90%, so a **cached re-walk from the top is
cheap** — the thing the checkpoint was protecting against is no longer expensive. Manual
**`--folder` scoping** replaces it for "just redo this part." The `modifiedTime`-based
freshness logic this would have needed was fiddly and risky (sync churn bumps
`modifiedTime`). Revisit ONLY if cached re-walks prove slow or frequent enough to matter.
Update (2026-06-05): the deferral premise ("cached re-walks are cheap") is weakened by
real cost data — routing is now ~88% of run cost. See #11 for the lighter alternative
(deterministic pre-pass, manifest-as-checkpoint) that targets the same re-walk cost
without mid-walk state.

### 3. Save-after-translate
- **(a) save immediately — Done.** Rule in `agent_routing_prompt.md`: on a successful
  translate, save in the same step. This is the **soft** guarantee — shrinks the
  translated-but-unsaved-on-crash window but doesn't eliminate it.
- **(b) disk-persist the cache — Open.** The **hard** guarantee: persist `CONTENT_CACHE`
  (translated md behind its handle) to disk so a crash after translate-before-save can
  recover the md without re-paying the translation. Still the right long-term fix.

### 4. Download integrity — **Done**
Downloads are integrity-checked (complete-bytes verification, not just "no exception").
Its **"verify md5" follow-on is now Done** — delivered as the md5 freshness gate (item
**#10** below; cross-linked). Note the separate, related truncation fix on the
*translation* side: `num_retries` does NOT catch a short-but-completed response; an
explicit length-verify loop does (full saga in HISTORY 2026-06-04).

### 5. Cross-ID dedup — **Done**
Dedup keys on source-content SHA **across Drive file IDs** — the same bytes re-uploaded
under a new ID are recognized as already-translated instead of re-paying.

### 6. Standing manifest-integrity audit — **Open (as a `--audit` mode)**
Idea: a standing `--audit` mode that walks the manifest and flags drift (missing md,
stale hashes, orphaned entries). Not yet a mode. NOTE: the audit *logic* was already
**run once manually** as the md5 backfill (2026-06-04) — it found the **one 404 orphan**
(source gone from Drive, entry left in place harmlessly) and **0 SHA mismatches**. So the
logic is proven; only the packaged `--audit` entry point is outstanding.

### 7. `translate_image_pdf` >2000px — **Known limit — NOT fixing**
Large image inputs can exceed the model's per-image pixel ceiling. Deliberate
non-fix: short handwritten lectures are **unaffected** (verified). The trigger is page
**COUNT** on large scans (>~50pp), not individual page size — normal lectures never hit
it. Revisit ONLY if a genuinely large scan (>~50pp) needs translating.

### 8. Routing-cost telemetry — **Done** *(was Partial; closed 2026-06-06)*
- **Done:** per-turn `cache_read` / `cache_write` logging — this is what closed the
  blind spot that let ~$39 of routing cost accumulate invisibly (the meta-lesson of the
  $60 post-mortem).
- **Done (the previously-open half):** the end-of-run summary now folds per-call input /
  output / cache tokens into a true total run cost (translation + routing in one number),
  with the routing-vs-translation split and token totals by type. Mechanism: new module
  `costs.py` + a per-call ledger (`logs/ledger_<run_id>.jsonl`, OTel-aligned), with an
  `on_usage` callback seam on the translate functions and the routing call recorded inline.
  Fixed a real bug in the process: the old `_calc_cost` billed `cache_read` tokens at the
  full input price (~10× routing overstatement); cost is now cache-tiered ($5 in / $6.25
  cache-write / $0.50 cache-read / $25 out). Feeds the Phase 2 email summary (plan step 2.9).

### 9. `already_pending` vestige — **Done**
The `already_pending` / `pending_approval` dedup branch was orphaned by the autonomy
reversal (nothing writes that status once `flag_for_approval` is gone). Per the build
record (HISTORY, Day 2 Part 4.3) it was **deleted from `read_file`'s branch logic AND
stripped from its schema description**. Marked Done on that basis.
> Caveat: confirmed against the documented build record, not re-read from live
> `read_file` source in this pass — if a stray reference resurfaces, reopen.

### 10. md5 freshness gate — **Done-verified** *(new item)*
Before downloading a file's bytes, compare Drive's `md5Checksum` to the stored
`source_md5`; if equal, **skip the byte download entirely** (source unchanged). Chosen
over `modifiedTime`, which is **unreliable on a synced folder** (sync churn bumps it
without a content change) — md5 is sync-immune. **Backfilled 97/98** manifest entries
with `source_md5`; backfill audit found **0 new stale entries** and the **one 404
dangling entry** (left in place harmlessly). Cross-links to #4 (this IS its "verify md5"
follow-on) and #6 (the backfill doubled as a one-off audit run).

### 11. Deterministic routing pre-pass — **BUILT (2026-06-06, uncommitted pending review)** *(was Open)*

**Built** as `prepass.py` — pure, no `anthropic` import, import-clean like `dedup.py` /
`costs.py`. `walk_tree` (recursive, `parent_path`, injected lister) · `diff_tree` (pure md5 diff
via `dedup`) · `build_worklist` (wires real Drive + manifest). Walks the tree via
`list_folder_children` (md5 metadata only, no downloads), diffs against the manifest, emits a
worklist of new/changed files only — unchanged (incl. marked skips) absent. Agent loop + tools
unchanged; kickoff hands the loop the worklist (`while True` → `while worklist`); empty worklist
→ end run, no spend. New tool `skip_file` (records `skipped_permanent` with `source_md5`); tool
count 7 → 8. Supporting: `drive.list_folder_children(include_md5=False)`, `dedup.skip_unchanged`,
`test_prepass.py` (7 tests), `test_dedup.py` 16/16. Prerequisites (lazy Drive, `dedup.py`) had
landed in the earlier 2026-06-06 optimization pass.

- **DATA gate — RESOLVED (premise was unstable, not confirmed).** The justification
  "routing ≈ 88% of run cost" was an **n=1 artifact** of a 1-new-file run. The 2026-06-06
  verification run (7 new files) split routing/translation **~50/50** ($1.958 / $1.954 of
  $3.91). The percentage moves with run composition — it is NOT a stable metric, so
  "confirm 88% across a few runs" is moot. Reframed to **absolute routing-$ on a near-static
  tree** (~$2/run, almost all re-derivation over unchanged files) — which the pre-pass
  eliminates by diffing deterministically. First live run: **scanned 117 → 15 worklist** (102
  unchanged excluded). The 2 bootstrap `skipped_permanent` entries lack `source_md5`, so they
  re-enter the worklist once; after re-skip via `skip_file` (stores `source_md5`) they drop for
  free. See HISTORY "Deterministic routing pre-pass built + skip-rule narrowed (2026-06-06)".
- **TIMING.** The win recurs with run frequency — fully justified alongside the Phase 3
  cron (~180 runs/yr), marginal under rare manual runs.

The routing half of run cost now dominates: a 2026-06-05 test run that translated one new
file cost **~$1.22 total — $0.154 translation, $1.07 routing** (24 turns / ~130 tool
calls). The md5 gate (#10) already made file I/O nearly free (**94/95 files gate-skipped**,
zero byte downloads), so the remaining cost is purely the agent re-walking and re-reasoning
over a near-static tree every run.

Root cause: **the agent loop is the tree walk.** The project bet on semantic routing
("agent decides, not lookup tables"), so every run re-derives `course` / `type` / `md_path`
decisions for files already recorded in `translated_log.json`. That re-derivation is not
novel judgment — it's a lookup already stored in the manifest.

Canonical fix (world-knowledge convergent): **thin deterministic harness, LLM only on the
delta.** Established agent-architecture guidance is uniform — fixed/predictable sequences
belong in deterministic pipelines; the agent loop is for steps whose path can't be
predicted in advance. Split the work:
- **Deterministic code (≈free):** walk the tree, md5/hash-diff against the manifest via
  `dedup.py`, and emit a worklist of only new/changed files. Two clauses from the original plan
  are struck:
  - ~~apply skip-wins on folder names (פתור/פתרון/תשובות)~~ — **REVERSED 2026-06-06: skip-wins
    is NOT in the pre-pass.** Skip is now a *semantic homework-only* judgment (solved side of a
    HOMEWORK folder), which only the agent loop can make; the pre-pass stays skip-blind.
  - ~~re-apply the already-recorded classification for any file whose hash matches~~ —
    **STRUCK 2026-06-06 (vestigial):** a hash-match is UNCHANGED → absent from the worklist →
    never re-saved, so there is nothing to re-apply. The resolved model is simpler than #11
    originally described: **unchanged = absent, full stop** — no re-classification step.
  Unchanged files (including deliberately-skipped solved homework, once recorded
  `skipped_permanent`) are absent — **absence is the signal**. See HISTORY "Skip-rule
  narrowed … (2026-06-06)".
- **Agent loop (Opus 4.8, delta only):** receives the worklist — new/changed files whose
  Hebrew name/content doesn't match a known pattern, plus new-course auto-naming. The
  existing loop and its 6 tools don't change; they just receive a 1-file worklist instead
  of a whole tree. On the test run that collapses ~24 turns to ~2.

This is **NOT** the rejected traversal checkpoint (#2). #2 was stateful mid-walk resume
(per-run checkpoint bookkeeping) — rejected because cached re-walks were thought cheap. The
cost data weakens that premise (caching flattened but did not remove the re-walk cost). The
pre-pass needs no new mid-walk state: **the manifest already is the checkpoint.**
Deterministic code diffs against it; the agent never sees the 94 unchanged files.

Preserves the semantic-routing bet where it earns its cost — new files still get full
semantic classification (the part that resisted lookup tables: plurals, construct forms,
synonyms). It only stops re-paying Opus to re-classify files already in the manifest.

Cross-links: #10 (md5 gate — solved the download re-cost; this solves the re-walk
re-cost), #8 (the cost ledger — the data gate that must confirm the ~88% split first),
#2 (the heavier rejected alternative).
