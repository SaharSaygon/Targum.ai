# STATUS

## Current state
**Phase 1 COMPLETE.** The full agent loop is built and running end-to-end on real
Drive folders: autonomous on naming + routing (no approval gate), budget-guarded
(200 tool-call cap), audit-logged (loud decision log), and crash-resilient
(crash-safe end-of-run summary). All seven tools work: the 6 core handlers
(`list_folder`, `read_file`, `translate_text_pdf`, `translate_image_pdf`,
`save_to_vault`, `update_mapping`) plus `fetch_signal_detail` for
offloaded-signal retrieval.

Semester ד is fully translated — **98 files** (manifest entries with `md_path`, incl. the one 404 orphan), out of **104** total entries (the other 6 = 4 `not_translated_yet` seeds + 2 `skipped_permanent`).

**Prompt caching VERIFIED live**: `cache_read` climbs turn over turn; steady-state
cost is ~2 input tokens per turn once the cache is warm.

**Optimizations landed:**
- **Save-immediately** rule — translate → save in the same step (soft side of Gap 3);
  shrinks the window where a translated-but-unsaved file is lost to a crash.
- **Context-offloading** — verbose detector signals (`per_page`,
  `unrecognized_sample`) live behind handles; the agent retrieves them on demand
  via `fetch_signal_detail` instead of carrying them in message history.
- **md5 freshness gate + backfill** — skip the byte download when Drive's
  `md5Checksum` matches the stored `source_md5`. Backfilled coverage: **98/98** (every `md_path` entry now carries `source_md5`).
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
re-eval note. Tool set unchanged — still **7** (the deleted scripts were never tools).

## Next step
**The deterministic routing pre-pass (PHASE2_NOTES #11) is the next planned architectural
work, and it is now unblocked** — both prerequisites landed in the 2026-06-06 pass: lazy Drive
init (`import drive` runs without OAuth) and `dedup.py` (the dedup decision is now a pure,
API-key-free helper the pre-pass can call directly). The agent loop and its 6 tools don't
change; the pre-pass diffs the tree against the manifest deterministically and hands the loop
a worklist of only the new/changed files.

Two gates remain before building it:

1. **DATA.** Let the new cost ledger log **3–4 real runs** to confirm the routing-vs-
   translation split. The pre-pass's entire justification is "routing ≈ 88% of run cost,"
   which is currently **one hand-computed observation**, not a distribution. Confirm it with
   real logged data first.
2. **PAYOFF / TIMING.** The savings *recur with run frequency* — fully justified alongside
   the Phase 3 cron (~180 runs/yr), marginal under rare manual runs. Build it when cron is
   imminent, not on a manual-trigger cadence.

So: **NEXT, pending a few logged runs + cron timing** — not a vague deferred item.

## Deferred (not blocking)
- Disk-persisted cache as the HARD save-after-translate guarantee (the soft
  save-immediately rule is the current mitigation — see PHASE2_NOTES #3b).
- Standing manifest-integrity `--audit` mode (the md5 backfill was a one-off
  manual run of this logic — PHASE2_NOTES #6).
