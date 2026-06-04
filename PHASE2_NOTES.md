# Phase 2 — Resilience & Cost Notes

Items deferred from Part 4 (the agent loop). Each is grounded in a real incident
from the build-out runs. Status: **Done** (landed), **Open** (not built),
**Verify** (built but not yet confirmed on a funded run).

---

## 1. Prompt caching on the routing loop — **Done — verified live**

**Why:** the dominant cost on a long run was *not* translation (~$21 tracked) but
the routing agent's own per-turn `client.messages.create` calls (~$39 untracked,
~60% of the ~$60 bill). With no caching, every turn re-sent the static system
prompt + 6 tool schemas **plus the entire growing conversation history** at full
input price — ~180 turns across 5 runs, each re-paying for a large, mostly
unchanged prefix.

**What landed (`agent.py`):** two ephemeral `cache_control` breakpoints —
- `SYSTEM_CACHED`: breakpoint on the system block → caches tools+system together
  (render order is tools → system → messages).
- `_set_cache_breakpoint(messages)`: a breakpoint moved onto the last message's
  last block each turn → caches the growing conversation prefix. Clears the prior
  marker first, so the request always carries 2 breakpoints (cap is 4).
- Per-turn logging of `cache_read` / `cache_write` tokens for verification.

**Economics:** cache reads ≈ 0.1× input, writes ≈ 1.25×. Should turn most of the
~$39 routing cost into a few dollars on a long run.

**Verified live (Design of Algorithms scoped run, 2026-06-04):** `cache_read`
climbed as designed — 0 on turn 1 (cold), then 5150 → 5567 → 9559 → … with
`input_tokens` pinned at ~2 on every hit turn (the whole prefix served from
cache). A static-prefix audit was clean first: system prompt read once from
file, `TOOLS` a static literal, model constant, mappings/date/counters all live
after the breakpoint or outside the prompt entirely, no `set()` / nondeterministic
JSON in any cached block. Mechanism confirmed working.

**Real cause of the cache misses observed — 5-minute TTL, NOT the 20-block lookback.**
(Earlier draft of this note blamed the lookback; that was wrong — corrected here.)
`cache_read` dropped to 0 on exactly the two turns that *followed* a
translation-heavy turn (turn 7 dispatched 5 translations; turn 8 dispatched 8).
Each routing call writes the cache, then the loop spends MINUTES executing that
turn's translations before the next routing call — those gaps exceeded the
300-second ephemeral cache lifetime, so the next turn read 0 and re-wrote. The
clincher against a lookback explanation: turn 11 batched **11 `read_file` calls**
(a large content-block batch) and the next turn still **HIT** — block count did
not cause misses; *wall-clock time during translation batches* did. The repeated
expire-and-rewrite (paying the 1.25× write premium *without* the 0.1× read) is
why the routing saving came in at only ~25% on that (short, translation-dense) run.

**When it matters / the fix.** A *typical 3–4 file run* has one short translation
turn (~1.5–2.5 min for text) — **under** the 5-min window — so the cache holds and
the default TTL is fine; **no change needed.** It only bites (a) big catch-up
sweeps that batch many translations in one turn, or (b) a run whose translate turn
is dominated by a large multi-page **image** scan (a single vision translation can
take 3–5+ min on its own). Fix for those: set
`cache_control: {"type": "ephemeral", "ttl": "1h"}` on **both** breakpoints — the
1-hour cache survives the long translation gaps. Trade-off: 1h writes cost 2×
input vs 1.25×, but on a workload that otherwise expires-and-rewrites every
translation turn, writing once and reading across the gaps is the clear net win.
**Recommendation:** keep the 5-min default for steady-state 3–4 file runs; flip to
`ttl: "1h"` before any full-tree / image-heavy sweep.

**Caveats / follow-ons:**
- Opus 4.8 min cacheable prefix is **4096 tokens**. System+tools alone (~3–3.5K)
  is borderline and may not write on its own; harmless because the conversation
  breakpoint's prefix includes tools+system and exceeds 4096 within a turn or two.
- **20-block lookback** (theoretical, did NOT manifest): a turn batching >20
  content blocks could in principle miss; this run disproved it as the observed
  cause (turn 11's 11-read batch still hit). Keep in mind only if a future run
  shows misses that do *not* correlate with long translation gaps.
- **Context keeps growing.** Caching makes re-sending cheap but the prefix still
  grows unboundedly (every `list_folder` dump and `read_file` signals blob stays
  in history). Pair with **context editing** (prune stale tool results) or
  **compaction** (summarize when near the window) to bound absolute context size,
  not just its per-turn cost. See the `claude-api` skill → agent-design /
  prompt-caching.

---

## 2. Traversal checkpoint / `--resume` / incremental — **Open**

**Why:** every resume re-walks the whole tree from the root (~95 files re-read)
because there's no checkpoint. Manifest dedup makes this *correct* (already-done
files return `already_done`) but **slow**, and — pre-caching — it also re-paid the
full routing cost to re-reason over the whole tree each time.

**Build:** persist traversal progress (e.g. processed folder IDs / a frontier) so
a resume skips already-walked subtrees instead of re-listing and re-reading them.
Complements caching: caching cuts the per-turn cost, a checkpoint cuts the number
of turns on a resume.

---

## 3. Save-after-translate (don't strand paid work) — **Open**

**Why:** the agent batches *translate* in one turn and *save_to_vault* in the
next. When the run died mid-batch (credit exhaustion), 9 finished translations
(~$2.33, real tokens spent) were stranded in the in-memory `CONTENT_CACHE` and
lost — paid for, nothing durable saved.

**Build options:** (a) prompt/route the agent to save immediately after each
translate, or (b) persist the markdown cache to disk so a crashed run's
translations survive and a resume can save them without re-translating. (a) is
simpler; (b) is more robust.

---

## 4. Download integrity check — **Done** (Step 2)

`drive.download_bytes` verifies the downloaded length against Drive's reported
`size` and retries (up to `_NUM_RETRIES+1`), raising `IOError` if it never
completes — so a silently-truncated PDF (the run-2 `BrokenPipe` casualty) can
never be hashed or translated. `read_file_logic` turns the raise into
`status:"error"`. Possible follow-on: also verify our md5 against Drive's
`md5Checksum` (stronger than length alone).

## 5. Cross-ID content dedup — **Done** (Step 3)

`read_file_logic` now also returns `already_done` when the fresh `source_hash`
matches **any** non-null-`md_path` entry (not just the same `drive_file_id`),
guarding against flaky re-downloads and true duplicate files. Safe because Step 4
(integrity) guarantees the hash came from complete bytes.

---

## 6. Standing manifest-integrity audit — **Open**

The one-off audit (compare each done entry's stored `source_content_hash` vs a
fresh integrity-checked download) found 1 truncation casualty (`Lecture13`,
Design of Algorithms — marked for redo). Worth a standing `--audit` mode that
re-runs this and flags any stored hash that no longer matches current Drive bytes
(truncation, or a legitimate re-upload — both mean the vault `.md` is stale).

## 7. `translate_image_pdf` page-dimension fix — **Open**

Large scanned PDFs fail the vision call: *"image dimensions exceed max allowed
size for many-image requests: 2000 pixels"*. At 200 DPI, page rasters exceed the
2000px long-edge cap that applies to many-image requests. Fix: downscale pages to
≤2000px before encoding, and/or chunk many-page PDFs across requests. Currently
**blocks large scanned lectures** (e.g. the 52-page Complex Functions L8 and
others), so they silently never translate.

## 8. Routing-cost telemetry — **Open**

The run summary's `total translation cost` only sums translation-engine
`cost_usd`; the routing agent's own token spend (the ~$39 above) is not recorded.
The new per-turn `cache_read`/`cache_write` logging is a start — fold per-turn
input/output/cache tokens into the end-of-run summary so the *true* run cost
(translation + routing) is visible, not just the translation slice.

## 9. `read_file` `already_pending` vestige — **Open** (cleanup)

The `read_file` tool schema description still mentions an `already_pending`
status, orphaned by the autonomy reversal (nothing writes a pending state since
`flag_for_approval` was removed). Strip it when next touching `read_file`.
