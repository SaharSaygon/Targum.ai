# OPTIMIZATION

Analysis-only pass. **No code was changed; no git operations.** Every finding cites
`file:line` and builds on `ARCHITECTURE.md` (repo root) where it already flagged
something. Priority order is **SIMPLE first, OPTIMAL second** — except where cost
dominates so heavily (routing ≈ 88 % of run cost, PHASE2_NOTES #11) that the cost
item outranks everything; that one is called out as the top lever.

Out-of-scope constraints from the brief are respected throughout and re-stated in
§6 (Blockers / do-not-touch) so nothing here collaterally violates them.

---

## 1. Cost / run-time optimizations (highest priority)

### Cost anatomy (grounded in PHASE2_NOTES #11, lines 86–115)
A 2026-06-05 run that translated **one** new file cost **~$1.22 = $0.154
translation + $1.07 routing**, over **24 turns / ~130 tool calls**. The md5 gate
(`agent.py:146-155`, PHASE2_NOTES #10) already made file I/O nearly free (94/95
files gate-skipped, zero downloads). So the $1.07 is **pure re-walk**: the agent
re-lists every folder and re-`read_file`s every file, emitting reasoning + tool_use
blocks each turn, to re-derive `course`/`type`/`md_path` decisions **already stored
in `translated_log.json`**.

Why caching (PHASE2_NOTES #1) doesn't already solve it: the two ephemeral
breakpoints (`agent.py:418-447`) make the *input* prefix cheap (~0.1× on reads),
but every turn still (a) **writes** new blocks to cache at ~1.25× and (b) emits
**output tokens** at $25/M for the agent's per-turn reasoning + tool_use. 24 turns
of output is the irreducible floor caching can't touch. The only way down is
**fewer turns / fewer tool calls.**

---

### Option A — Deterministic routing pre-pass (PHASE2_NOTES #11) — TOP LEVER

**What's being re-paid:** the entire tree walk for ~94 unchanged files — Opus is
re-classifying files whose classification is a lookup, not a judgment.

**Proposed change (concrete, against the actual loop + tools):**

The dedup decision already exists inside `read_file_logic` (`agent.py:135-186`):
md5 gate → manifest `find_by_id` → hash compare → `already_done`/proceed. Today it
runs *inside the agent loop, one LLM round-trip per file*. Lift the **decision
half** (everything except download+detect, i.e. `agent.py:146-185`) into a plain
Python function in a module that imports only `drive` + `manifest` (NOT `anthropic`):

```
# proposed: planner.py  (no Anthropic client, no loop)
def build_worklist(root_folder_id) -> list[Decision]:
    walk the tree via drive.list_folder_children   (recursive; agent.py uses it at handle_list_folder)
    prune SKIP folders by name      (פתור/פתרון/תשובות/מענה — the "Skip wins" rule, agent_routing_prompt.md:12,16)
    for each file:
        md5 = drive.file_md5(id)                    # metadata only, free
        entry = manifest.find_by_id(...)
        if md5 gate or SHA dedup hits  -> SKIP (re-apply stored course/type, no LLM)
        else                           -> ADD TO WORKLIST  (new/changed -> needs the agent)
```

The **agent loop and its 6 tools do not change** (PHASE2_NOTES #11, lines 106-108).
The kickoff (`agent.py:489-499`) changes from "walk folder ID X" to "here is a
worklist of N files that need decisions" — usually N=0 or 1. On the test run that
collapses **~24 turns → ~2** and **~130 tool calls → ~handful**.

**The delta that stays in the agent loop:** new/changed files only — full semantic
classification (plurals, construct forms, synonyms — the locked bet, HISTORY Day 2
Part 4), mode selection (text vs image), and new-course auto-naming + `update_mapping`.
Nothing semantic is replaced by a table; the table only short-circuits files the
manifest already answers.

**Expected cost impact:** order-of-magnitude. Routing collapses from ~$1.07 to the
cost of the worklist (≈ $0 when nothing changed, ≈ one file's classification when
one file is new). Translation cost is unchanged (it's the legitimate spend). Net:
a no-change re-run drops from ~$1.22 to **near-zero**; a one-new-file run from
~$1.22 to **~$0.15 + a few cents**.

**What must be preserved (semantic behavior):**
- New/changed files still get the agent's full semantic routing — the pre-pass must
  only *skip* files, never *classify* new ones with keywords (brief constraint).
- "Skip wins" (`agent_routing_prompt.md:16`) is the one rule the deterministic code
  replicates — and it already *is* a substring rule, not a judgment, so replicating
  it is faithful. Keep it conservative: prune only on the documented SKIP tokens.
- The md5/SHA dedup semantics must stay byte-identical to `read_file_logic` so the
  pre-pass and the in-loop path agree (extract one shared function; don't fork the
  logic — see §4).

**Failure modes (must be designed for):**
1. **Misclassified-but-hash-matched file silently skips re-evaluation.** If a prior
   run stored a wrong `course`/`type`, the pre-pass re-applies it forever because the
   hash matches. *This is not a regression* — the current loop already returns
   `already_done` on hash match (`agent.py:169-185`) and the agent moves on without
   re-judging. The pre-pass inherits identical semantics. Mitigation lives in the
   already-proven `--audit` logic (PHASE2_NOTES #6, run once as the md5 backfill):
   package it as the standing integrity check, not the hot path.
2. **CLEAN / inherit-from-parent files** (`agent_routing_prompt.md:13,17` — נקי inside
   מבחנים → exam) need parent context. Keep these OUT of the deterministic skip set
   unless their hash already matches a manifest entry; if unmatched, they go on the
   worklist for the agent. Don't reimplement inheritance deterministically.
3. **A changed file that md5-matches a stale entry** can't happen — md5 is
   content-derived (`drive.py:74-83`); a content change changes md5 → falls to the
   worklist. Safe by construction.
4. **Drive import-time OAuth** (`drive.py:39`) means the planner can't run headless
   without credentials — see §4 (lazy init is a prerequisite for clean pre-pass /
   test use).

**Locked-decision note:** this *reinforces* rather than reverses the semantic-routing
bet — PHASE2_NOTES #11 (lines 116-119) explicitly frames it as "stop re-paying Opus
to re-classify files already in the manifest" while keeping semantic classification
for new ones. It does retire the implicit "the agent loop IS the tree walk" posture
(PHASE2_NOTES #11 line 93); that retirement is the whole point and is sanctioned.

---

### Option B — Narrower system prompt
`agent_routing_prompt.md` is 58 lines; `SYSTEM_CACHED` (`agent.py:418-422`) caches
system+tools after turn 1. Trimming the prompt saves only the **one-time cold
cache-write** at ~1.25× of a few hundred tokens — negligible on any multi-turn run,
and *moot* once Option A makes runs ~2 turns. **Rank: low.** Not worth it as a cost
play (may still be worth it for clarity, but that's not this axis).

### Option C — Fewer signals in `read_file` output
`read_file`'s `ready` result (`agent.py:230-243`) only fires for files that actually
get translated — i.e. the *delta*. For the ~94 already-done files it returns the
tiny `{"status":"already_done","md_path":...}` (`agent.py:155,172`). So trimming the
signal payload saves little on a static-tree run. The real win adjacent to this is
**removing `fetch_signal_detail` + the `:signals_full` offload entirely** (0 calls
ever — STATUS line 35-36) — but that's a *simplicity* win (see §2.1), not a material
cost lever. **Rank: low for cost, medium for simplicity.**

### Option D — Cache TTL tuning
No `ttl` is set on the `create()` call (`agent.py:524-530` — confirmed: no `ttl`
key). Default is 5 min (PHASE2_NOTES #1, lines 12-14), which **expires mid-run on
slow image-heavy sweeps**, forcing a full-price re-write of the prefix. Setting
`cache_control: {"type":"ephemeral","ttl":"1h"}` on image-heavy sweeps avoids that.
**Rank: low-medium**, and **moot after Option A** (runs become too short to hit the
5-min wall). Cheap to add as a conditional; do it only if image-heavy full sweeps
remain a use case after the pre-pass lands.

### Ranking (cost axis)
**A (pre-pass) ≫ D (ttl, image-heavy only) > C (signal trim) ≈ B (prompt trim).**
A is the only order-of-magnitude lever; the rest are marginal and most are subsumed
once A lands. Everything in §2 is simplicity-first and cost-neutral-to-tiny.

---

## 2. Dead code / simplification

Each item grep-confirmed for removal safety.

### 2.1 `fetch_signal_detail` tool + the `:signals_full` offload — REMOVABLE
- Schema `agent.py:48-58`; handler `agent.py:250-258`; `HANDLERS` entry `agent.py:402`; cache write `agent.py:222-226`; `signals_full_handle` field in `read_file` return `agent.py:242`; prompt note `agent_routing_prompt.md:30`; mention in `read_file` description `agent.py:39`.
- **Safety:** grep shows references **only** in `agent.py` + the prompt — the model never invokes it (STATUS lines 35-36: "0 calls in practice"). No external caller. Removal-safe.
- **Effect:** deletes a whole wired-but-unused tool, shrinks `read_file`'s return, and lets the detector stop computing `per_page`/`unrecognized_sample` for offload (still computed at `pdf_mode_detector.py:170-178,188-189` — would just no longer be stashed).
- **Note (not a blocker):** this reverses the "context-offload" design (HISTORY Day 2; ARCHITECTURE §6). The 0-call evidence is exactly the justification STATUS itself queues for dropping it. Keep the **scalar** signals inline — those drive every mode call.

### 2.2 `_ROUTING_PROMPT` dead read in the engine — REMOVABLE
- `translation_engine.py:65-67` defines it; grep shows **no other reference**. ARCHITECTURE §8 #4/#8. It's an import-time file read with no consumer (the agent loads the routing prompt itself at `agent.py:20`).
- **Safety:** zero callers. Removal-safe and removes an import-time `read_text`.

### 2.3 Duplicate `sha256_of` (three copies) — CONSOLIDATE
- `manifest.py:23`, `translation_engine.py:95`, `init_translation_log.py:133` — identical `"sha256:" + hexdigest`.
- **Callers:** `manifest.sha256_of` ← `agent.py:165`. `engine.sha256_of` ← `translate_one.py:82`, `translate_image_pdf.py:83`. `init`'s copy is self-contained (`init_translation_log.py:454`).
- **Proposal:** make `manifest.sha256_of` the single source; have the engine `from manifest import sha256_of` (or re-export). If the manual scripts are deleted/re-homed (§3), `engine.sha256_of` loses its only callers and can go too. `init`'s copy is a run-once bootstrap artifact — leave it (it predates `manifest.py`; touching it has no payoff).
- **Safety:** behavior is byte-identical across copies; consolidation is mechanical.

### 2.4 `save_to_vault` required-but-ignored `filename` param — DROP FROM SCHEMA
- Schema declares it `agent.py:94` and **requires** it `agent.py:104`; `handle_save_to_vault` (`agent.py:339-385`) never reads `inp["filename"]` — the output stem comes from `drive_filename` via `vault_output_path` → `Path(drive_filename).stem` (`translation_engine.py:147`). ARCHITECTURE §8 #6.
- **Safety:** removing it from the schema changes no behavior (it's discarded). It's a model-facing field, so the model currently **wastes output tokens** generating a value that's thrown away — a tiny cost + clarity win.
- **Proposal:** remove `filename` from `properties` and `required`.

### 2.5 Unused `drive_file_id` param on the translate engine fns — LOW-PRIORITY DROP
- `translation_engine.py:180` (`translate_text_pdf`) and `:248` (`translate_image_pdf`) declare `drive_file_id`; the agent passes `""` (`agent.py:282`, commented "unused by the engine's translate path"). Unused inside both functions.
- **Safety:** dropping it changes the signature → must update the two call sites in `agent.py` and the manual scripts. Low payoff; do it only if touching these signatures anyway. Keeping it is harmless (uniform signature).

### 2.6 Stale comments naming nonexistent files — DELETE (comment-only)
- `agent.py:1-2` references `stage1_bare_turn.py` / `stage2_loop.py` (don't exist). `agent.py:121-122` says handlers are "still stubbed" — untrue, all are real (ARCHITECTURE §1, §8 #1).
- **Safety:** comments only. Zero risk.

### 2.7 Doc string wrong: `model="opus-4-8"` — FIX
- `skills/save-to-vault.md:12` says record `model="opus-4-8"`; code records `cost_data["model"]` = `"claude-opus-4-8"` (`translation_engine.py:26,383`; manifest confirms). ARCHITECTURE §8 #5.
- **Safety:** doc-only fix; align the skill text to the real value.

---

## 3. Divergent-path consolidation

### 3.1 `translate_one.py` / `translate_image_pdf.py` — divergent second path
These contradict two locked invariants and the agent's path model:
- **Direct manifest write, bypassing `manifest.upsert_entry`:** `translate_one.py:104-119` and `translate_image_pdf.py:110-125` do remove-then-append (`log = [e for e in log if ...]; log.append(...)`). The "sole writer + in-place upsert" lock lives in `manifest.py` (docstring lines 1-10, `upsert_entry` 62-75) and `skills/save-to-vault.md:12`. ARCHITECTURE §8 #9.
- **Save to course root, no type subfolder:** `translate_one.py:97` / `translate_image_pdf.py:105` write `vault/<course>/<stem>_EN.md`, skipping the `TYPE_TO_FOLDER` routing the agent uses via `vault_output_path` (`translation_engine.py:111-150`). Different on-disk layout than every agent-produced file.
- **Duplicate Drive plumbing:** each carries its own `get_drive_credentials` + `download_bytes` (`translate_one.py:33-57`, `translate_image_pdf.py:33-57`), duplicating `drive.py:23-71`.
- **Stale-evidence smell:** their `DRIVE_FILE_ID`s differ by one trailing char (`translate_one.py:25` `...JQNju` vs `translate_image_pdf.py:25` `...JQNjuP`) — a copy-paste artifact suggesting neither has been run recently. ARCHITECTURE §1.

**Does the agent supersede them?** Mostly. The agent does autonomous full-tree
translation and **picks mode itself**. What these scripts uniquely offer is a
**force-mode single-file** run (force text or force image, bypassing the detector) —
there is no such escape hatch in the agent. So they have a narrow niche but a
wrong implementation.

**Proposal (ranked):**
- **(b) Re-home (preferred):** rewrite both as thin CLIs that call
  `engine.translate_text_pdf`/`translate_image_pdf` then **`engine.save_to_vault(...)`**
  (which already does atomic write + `vault_output_path` subfoldering + `manifest.upsert_entry`).
  This kills the divergent manifest write, the wrong path model, and the duplicate
  Drive plumbing (they'd import `drive.download_bytes`) in one move, while keeping
  force-mode. Effort: low-medium.
- **(a) Delete:** if force-mode-single-file isn't a real workflow, delete both —
  the agent covers translation. Simplest, but loses the capability and the only
  callers of `engine.infer_type` (`translate_one.py:111`, `translate_image_pdf.py:117`)
  and `engine.sha256_of`, which would then also become dead (§2.3) — a clean cascade.
- **Blocker note:** option (a) deletion is the *only* place `engine.infer_type`
  (`translation_engine.py:101-108`) is called outside `init`; deleting the scripts
  makes `infer_type` dead too (the agent passes `file_type` explicitly and never
  infers). That's a *bonus* simplification, not a blocker — just sequence it.

### 3.2 `list_drive.py` — superseded debug lister
- `list_drive.py` prints name+id, non-paginated (`list_drive.py:26-37`), builds its own auth (`:11-23`). Superseded by `drive.list_folder_children` (`drive.py:86-111`), which is paginated and returns type+mime. Imported by nothing (grep-confirmed).
- **Proposal:** delete. A one-liner over `drive.list_folder_children` replaces it if a quick lister is ever wanted. **Rank: low payoff, trivial effort.**

### 3.3 `hello_world.py` — smoke-test artifact
- One Sonnet-4.6 call (`hello_world.py:9`), imported by nothing, last touched 2026-04-20 (oldest file). A tutorial leftover.
- **Proposal:** delete (harmless either way). **Rank: trivial.**

### 3.4 `init_translation_log.py` — run-once bootstrap (keep, note the duplication)
- 549 LOC, self-contained, already executed (seeded the `manual`/`not_translated_yet`/`skipped_permanent` entries). Carries a **third** copy of Drive auth + `download_bytes` (`init_translation_log.py:65-131`) and `sha256_of` (`:133`).
- **Proposal:** **keep as-is** — it's a historical one-shot; re-homing it onto `drive.py`/`manifest.py` has near-zero payoff and risks the bootstrap record. Just *note* it as the third Drive-auth duplicate. Do **not** fold its manifest-entry construction into anything (would risk the do-not-migrate constraint, §6).

---

## 4. Module-boundary / coupling smells

### 4.1 `drive.service` builds at import → OAuth side-effect (`drive.py:39`)
`service = build(..., credentials=get_credentials())` runs at module import, so
`import drive` — and transitively `import agent` (`agent.py:11`) — triggers Google
OAuth. Consequences:
- The **pre-pass (Option A)** needs Drive callable; importing `drive` works but drags
  OAuth into any context that imports it, including tests/CI.
- **Proposal:** lazy init — a module-level `_service=None` + `get_service()` singleton
  that builds on first use. Pure refactor; all current call sites (`drive.py:54,62,80,94`)
  switch to `get_service()`. **This is a prerequisite for a clean pre-pass and for any
  importable test of `drive`/`manifest` logic.** Rank: low effort, enabler.

### 4.2 `Anthropic()` builds at import (`agent.py:17`)
Importing `agent` requires `ANTHROPIC_API_KEY` and instantiates the client. For the
pre-pass to run **without an API key** (it does no LLM work), the dedup decision must
**not** live in a module that imports `anthropic`. Today it's inside
`read_file_logic` in `agent.py` (which imports the client). **Proposal:** extract the
md5/SHA dedup decision (`agent.py:146-185`) into a `anthropic`-free helper (e.g. in
`manifest.py` or a new `planner.py`) consumed by *both* `read_file_logic` and the
pre-pass — single source of dedup truth (ties to Option A failure-mode #4 and §2/§3
"don't fork the logic").

### 4.3 Other import-time side-effects (benign, note only)
- `SYSTEM_PROMPT = ...read_text()` (`agent.py:20`); skills loaded at import
  (`translation_engine.py:55-67`); `LOG_PATH`/`COURSES_PATH` resolved at import. These
  are cheap local file reads, fine. The one to drop is the **dead** `_ROUTING_PROMPT`
  read (`translation_engine.py:65-67`, §2.2) — an import-time read with no consumer.

### 4.4 "Sole owner" is aspirational, not enforced
`manifest.py` claims sole ownership, but `init_translation_log.py` and the two manual
scripts build/write manifest entries directly (§3.1). Routing all writes through
`manifest.upsert_entry` (via re-homing in §3.1) makes the lock real. No new abstraction
needed — just stop bypassing it.

---

## 5. Per-item ranking table

Sorted by payoff-to-effort (highest first). "Locked?" = touches a HISTORY/plan
locked decision.

| # | Change | Axis | Payoff | Effort | Locked decision touched? |
|---|--------|------|--------|--------|--------------------------|
| 1 | Drop `filename` from `save_to_vault` schema (§2.4) | both (tiny cost) | low-med | trivial | N |
| 2 | Remove dead `_ROUTING_PROMPT` read (§2.2) | simplicity | low | trivial | N |
| 3 | Delete stale comments (§2.6) + fix `opus-4-8` doc (§2.7) | simplicity | low | trivial | N |
| 4 | Lazy `drive.service` init (§4.1) | simplicity + **enabler** | med | low | N |
| 5 | Remove `fetch_signal_detail` + `:signals_full` (§2.1) | simplicity (tiny cost) | med | low | **Y** — reverses context-offload design (HISTORY Day 2); 0-call data justifies |
| 6 | Delete `list_drive.py` + `hello_world.py` (§3.2/3.3) | simplicity | low | trivial | N |
| 7 | Consolidate `sha256_of` to `manifest` (§2.3) | simplicity | low | low | N |
| 8 | Re-home `translate_one`/`translate_image_pdf` through engine/manifest (§3.1) | simplicity | med | low-med | **Y** — restores "sole writer + in-place upsert" lock (good); cascades `infer_type`/`engine.sha256_of` to dead |
| 9 | `ttl:"1h"` on image-heavy sweeps (§1 D) | cost | low-med (image-only; moot after #10) | trivial | N |
| 10 | **Deterministic routing pre-pass (§1 A)** | **both (cost-dominant)** | **very high (~88% routing → near-zero on no-change runs; ~24 turns → ~2)** | high | **Y** — retires "agent loop IS the tree walk" (PHASE2_NOTES #11 sanctions); **preserves** semantic-routing bet for new files |

**Reading the table:** items 1-7 are quick simplicity wins, mostly cost-neutral,
no semantic risk (5 is the only design reversal, and it's evidence-backed). Item 8
is the consolidation that makes the "sole writer" lock real. **Item 10 is the only
large-payoff cost lever** — high effort, but it's where ~88% of run cost lives, so by
absolute payoff it's #1; it sits last in the ratio sort purely because of effort.
Prereqs for 10: items 4 (lazy Drive) and the §4.2 extraction of the `anthropic`-free
dedup helper. Sequence: **4 → §4.2 extract → 10**, with 1-3,5-9 landed independently
at any time.

---

## 6. Blockers / out-of-scope (do-not-touch) — restated for safety

These were declared OUT OF SCOPE; nothing above changes them. Flagged where another
change could collaterally hit them:

- **No `translated_log.json` normalization/migration.** The 4 historical entry shapes
  (ARCHITECTURE §5) are by design; rewriting legacy/manual/opus-4-7 entries into the
  full agent shape would fabricate `chosen_mode`/signals the detector never produced
  (refuse-rather-than-reconstruct). *Collateral watch:* §3.1 re-homing must keep
  writing the **same field set** `engine.save_to_vault` already writes — read-tolerance
  only; do not "upgrade" old entries on touch.
- **Do not touch `max_tokens=4096`** on the loop call (`agent.py:526`). The loop only
  emits tool-use + reasoning; translation runs at `max_tokens=16000` in the engine
  (`translation_engine.py:215,309`). Not the truncation surface. No item above changes it.
- **Do not strip the detector's dead threshold constants** (`pdf_mode_detector.py:28-30`)
  — intentional reference notes. §2 deliberately omits them.
- **Do not re-run/re-translate the 15 opus-4-7 or 36 manual entries** — historical
  record. The pre-pass (§1 A) explicitly *skips* them (hash match → re-apply stored
  classification); it never re-translates them.
- **Preserve the semantic-routing bet for new/changed files.** §1 A only short-circuits
  files already in the manifest; new files keep full semantic classification (plurals,
  construct forms, synonyms). The deterministic code must never classify a *new* file
  by keyword — only skip *known* ones and apply the narrow "Skip wins" folder prune.

---

## Sequencing summary

1. **Free wins now (no deps):** items 1, 2, 3, 6, 7, 9 — pure deletions/fixes.
2. **Enablers:** item 4 (lazy Drive init) + extract the `anthropic`-free dedup helper (§4.2).
3. **Consolidation:** item 8 (re-home manual scripts) — also retires `infer_type`/`engine.sha256_of` as dead.
4. **Design cleanup:** item 5 (drop `fetch_signal_detail`) — independent, evidence-backed.
5. **The lever:** item 10 (deterministic pre-pass) — built on (2)'s enablers; the ~88% cost win.
