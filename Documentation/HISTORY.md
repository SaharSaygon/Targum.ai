# HISTORY
Append-only log of findings, decisions, and calibration data.
Current state lives in STATUS.md — don't update status flags here.

---

## Environment setup

### Windows (initial)
- Python 3.12+, VS Code, Git (with user.name + user.email), Node.js + npm
- Claude Code v2.1.114 — required PATH override in VS Code settings.json
- Default VS Code terminal set to Command Prompt

### Mac transition (current machine — MacBook Air M5)
- Homebrew, Python 3, Node, Git, VS Code, Claude Code, gh installed
- Repo cloned from GitHub
- `.venv` recreated, `pip install -r requirements.txt` clean
- New Mac-specific Anthropic API key created; old Windows key revoked
- `credentials.json` re-downloaded from Google Cloud Console
- Obsidian vault path: `/Users/saharsaygon/Documents/Obsidian Vault/`

### Python version issue (caught during Day 2 calibration)
- ⚠️ `.venv` was built with system Python 3.9.6, not 3.12 as the plan specifies
- Surfaced via `google-auth` `FutureWarning` during `translate_one.py` run
- Not yet breaking, but Day 2 adds `pdf2image` + vision calls — more library surface area
- **Fix**: `brew install python@3.12`, rebuild venv with `python3.12 -m venv .venv`, reinstall reqs
- Resolved 2026-05-18: venv rebuilt on Python 3.12, requirements reinstalled clean. google-auth FutureWarning gone.

---

## Project repo (GitHub)

- Private repo: `Agent_Translator_English_MD`
- `.gitignore` excludes: .env, credentials.json, token.json, __pycache__/, .venv/, *.log, .DS_Store
- `requirements.txt` committed: anthropic, python-dotenv, google-api-python-client, google-auth-httplib2, google-auth-oauthlib, pypdf, pdf2image, Pillow

---

## API & auth setup

### Anthropic API
- API key created, credits added
- `.env` with `ANTHROPIC_API_KEY=sk-ant-...` (verified NOT in git status)
- `hello_world.py` runs, prints translation of "שלום עולם"

### Google Drive API
- Google Cloud project created, Drive API enabled
- OAuth consent screen configured, self added as test user
- OAuth credentials (Desktop app type) downloaded as `credentials.json`
- `list_drive.py` authenticates, lists iPad notebook folder
- `token.json` generated on first OAuth flow (correctly gitignored)

---

## Translation pipeline

### Mapping & system prompt
- `courses.json` — Hebrew course names → English mappings
- `subfolder_types.json` — **dropped**.
- `translation_system_prompt_agent.txt` — adapted from "Translating Hebrew Lectures" Claude Project, with Agent Routing & Traversal section

### `translate_one.py` (linear proof-of-concept)
- Downloads file from Drive by ID
- Extracts text with pypdf
- Calls Opus 4.7 with system prompt
- Saves `.md` to course folder per courses.json
- ⚠️ Saves to course root only — no subfolder routing (Lectures/Tutorials/etc). Expected: subfolder routing is agent work (Day 2 Part 4).
- ⚠️ **Does not detect handwritten PDFs** — happily runs pypdf text extraction on handwritten lecture templates and produces fluent-looking but heavily reconstructed output (see Linear Systems Lecture 4 below). This is Day 2 Part 1 territory.
- `max_tokens` set to 16000 (8000 was insufficient)
- ⚠️ Does not update `translated_log.json` after a translation — by design

### State manifest: `translated_log.json`

A JSON array tracking every file the agent has seen, with three jobs:

1. **Inventory** — every file in scope, with Drive ID, filename, and SHA-256 hash of source bytes
2. **Dedup** — hash-based "already translated" detection. Better than checking ".md exists" because it catches re-edits of source PDFs (hash changes → retranslate)
3. **Skip persistence** — files marked `skipped_permanent` with `skip_reason` are remembered across runs

Schema per entry:
```
drive_file_id, drive_file_name, source_content_hash,
md_path, course, type,
translated_at, model, skip_reason (optional),
cost_usd, input_tokens, output_tokens
```

Status values for `model`: `manual`, `not_translated_yet`, `skipped_permanent`, *(future)* `opus-4-7`.

This file is committed to the repo.

### Bonus translations completed (manual / pre-agent)

~25 files already translated via manual workflow before the agent exists. Logged in `translated_log.json` with `model: "manual"`. Includes:
- Design of Algorithms: Lectures 1–7, GraphDefinitions, ps 1–4, homework 1
- Introduction to Linear Systems: Lectures 1–3, Tutorials 1–3
- Introduction to Semiconductor Devices: Lectures 2–6, Tutorials 1–3

### Calibration translations

| File | Course | Source | Quality verdict | Notes |
|------|--------|--------|-----------------|-------|
| Lecture 9 | Design of Algorithms | typed PDF | 85–90% | First end-to-end. Math, structure, glossary, translator notes solid. |
| Tutorial 3 (Liran) | Complex Functions | typed PDF (Hebrew encoding broke pypdf) | **Acceptable, math reliable** | Heavy prose reconstruction (pypdf garbled Hebrew). Math correct end-to-end. YAML code-fenced (fixed manually). Fake `date_translated`. Section heading promoted to "Theorem" (minor). |
| Lecture 4 | Introduction to Linear Systems | **handwritten template** | ⚠️ **NOT acceptable — substantially reconstructed** | Source is handwritten lecture-template PDF. pypdf returned OCR garbage; model filled gaps from "standard manipulations". Translator Notes openly list 7+ proofs that were reconstructed. **Figure descriptions invented** (`![Figure: ...](not_included)`). Should not be counted as translated. Move back to `not_translated_yet`, redo via image mode (Day 2 Part 1). |
| Lecture 4 (image-mode redo) | Introduction to Linear Systems | 10-page handwritten template (blue-ink derivations in typed scaffolding) via vision @ 200 DPI | **Net positive — accept with caveats** | PDF-verified: handwritten proofs on pages 1, 2, 3, 5, 6, 8, 9 all transcribed correctly. Hand-drawn spectrum (page 3) described from image. Two regressions: (a) date_translated fabricated as "2025-01-21" — Issue 2 rule failed under vision load. (b) Blank $Z_1(\omega)$ box on page 4 filled with derivable answer (Context-flagged, but violates Issue 7). |

### `init_translation_log.py` (formerly `bootstrap_log.py`)

One-off script that seeded `translated_log.json` with the existing translated corpus. Renamed and documented with a header comment explaining it's a one-shot seeding script, not for regular use.

---

## Calibration round findings (Day 2 Part 0, Step 0.3)

The two calibration translations surfaced more than just prompt issues — they revealed a **tooling boundary** that needs to be enforced before more translations happen.

### 1. YAML code-fence wrapping — confirmed cross-file

Both translations opened with `` ```yaml `` ... `` ``` `` fence wrapping the frontmatter. Obsidian renders this as a code block; no metadata is parsed. Manual fix: replace fences with `---` delimiters.

**Verdict**: prompt-wide, not file-specific. Locked in for Step 0.4 revision.

### 2. Fabricated `date_translated` — confirmed cross-file

- Tutorial 3: `2025-01-29` (real date: 2026-05-06)
- Lecture 4: `2025-01-21` (real date: 2026-05-06)

Both wrong. Both look like training-data dates. Day 1's added rule ("today's actual date, omit if unknown") is not strong enough — model defaults to plausible-looking date when unsure.

**Verdict**: prompt rule needs strengthening. Probably: "if you don't know today's date with certainty, omit the field entirely — never guess. The agent loop will inject today's date as a system message; if it's not in your context, omit."

### 3. ⚠️ Critical finding: text-mode on handwritten PDFs produces deceptive output

This is the most important finding of the round. **Linear Systems Lecture 4 is a handwritten lecture template** — typed scaffolding (course name, lecture number, section headings) plus handwritten math filled in by the professor. pypdf can read the typed parts but only returns OCR garbage for the handwritten regions (visible in the input as `right infty int left wdeftright...` etc).

What the model did:
- Recognized that math sections were missing
- **Reconstructed them from "standard well-defined manipulations"**
- Listed every reconstruction in Translator Notes (commendable honesty)
- **Also invented figure descriptions** like `![Figure: Spectra in the AM modulation chain. Top: baseband spectrum...](not_included)` — without ever seeing the figures

The output reads like a clean, complete Fourier-transform-properties chapter. The math is correct because the model knows the topic. But it is **not a translation of this professor's lecture** — it's a reconstruction of what a generic lecture on this topic looks like. If the professor used non-standard notation, an unusual derivation, or a domain-specific simplification, it's gone.

**Honesty in Translator Notes does not redeem the output for study use.** A student opening this `.md` to review for the exam will get standard textbook material, not their professor's actual lecture.

**Implications:**
- `translate_one.py` must not be the path for handwritten content
- The auto-detect heuristic (Day 2 Step 2.1) becomes load-bearing — it's the only thing standing between handwritten files and silent fabrication
- The system prompt should add a refusal rule: if extracted text appears to be heavy OCR garbage, the model must refuse rather than reconstruct
- Lecture 4 should be reverted in the manifest (set back to `not_translated_yet`), then redone via image mode after Day 2 Part 1

### 4. Other smaller issues observed

- **Section heading promoted to "Theorem"** (Tutorial 3): source labeled "גזירות ומשוואות קושי-רימן" (Differentiability and Cauchy-Riemann equations — a section heading), model rendered as "Theorem (Cauchy–Riemann)". Minor faithfulness drift.
- **Markdown leak in prose** (Tutorial 3 Translator Notes): `"I added a brief expansion check as an > **Explanation:**"` — blockquote syntax leaking into note text.
- **Figure invention** (Lecture 4): see #3.

### Status of Day 1 known issues — recurrence in this round

| Day 1 issue | Tutorial 3 | Lecture 4 (text-mode) | Lecture 4 (image redo) |
|---|---|---|---|
| 1. Conversational preamble | Not observed | Not observed | Not observed |
| 2. Fake `date_translated` | ⭐ Recurred | ⭐ Recurred | ⭐ Recurred |
| 3. Filename wins over header | N/A (no conflict) | N/A (no conflict) | N/A (no conflict) |
| 4. Intermediate / redo output | Not observed | Not observed | Not observed |
| 5. Figure descriptions weak (text mode) | N/A (no figures) | ⭐ Recurred + worse: invented | Resolved — spectrum described from image |
| 6. **NEW: YAML code-fence wrapping** | ⭐ Observed | ⭐ Observed | Not observed |
| 7. **NEW: Reconstruction on handwritten PDFs** | N/A | ⭐ Severe | Mostly resolved — one blank box filled |
| 8. **NEW: Markdown syntax leaking into prose** | ⭐ Minor | Not observed | Not observed |

---

## Lecture 4 redo findings (Day 2 Part 1)

Image-mode pipeline works. Same source that text-mode hallucinated; this run source-verified against PDF.

**Vision genuinely reads handwriting** — substantial blue-ink content on pages 1–3, 5, 6, 8, 9 (proofs of Properties 1–3, 6–8, 10–12, plus $U(\omega)$ limit calc). All transcribed correctly — not topic-prior reconstruction. Hand-drawn spectrum on page 3 described from image, not marked `not_included`. Vision preamble override (added in translate_image_pdf.py to counter text-mode default) worked.

**Regression (a): date_translated fabricated under vision load. RESOLVED.**
Lecture 13 (text-mode, post-revision) correctly omitted it. This run fabricated "2025-01-21" — same pre-revision pattern. Hypothesis: ~50K image-input tokens weaken attention to micro-rules. Fix: inject today's date as a user-message field in translate_image_pdf.py (code-level — model can't fabricate what it's told). More reliable than tightening the prompt further. Verified resolved: L4 retranslate (2026-05-18) frontmatter has correct today's date — code-level date injection in `translation_engine.py` works.

**Regression (b): blank box on page 4 filled. DEFERRED — not currently blocking.**
Empty $Z_1(\omega) =$ box that students fill during class. Model combined the time-domain expansion above with the spectrum below, filled the box, Context-flagged it. Correctly left a separate "נשים לב" box blank — behavior isn't uniform; it fills when surrounding info is sufficient to derive. When picked up: add explicit example to Issue 7: "If the source contains a blank box, empty field, or placeholder, leave it blank — even if derivable from context. Mark as `[blank in source]`." Will revisit after Day 2 Part 4.

### Pending measurement

- L4 image-mode cost: **$1.52, 111 seconds** (recovered from integration test run). First real image-mode cost reference for per-semester budget projection — was ~$40 text-only; revise once 3–4 more image-mode files are translated.
- Confirmation that `translate_image_pdf.py` wrote the Lecture 4 entry to `translated_log.json` (drive_file_id, source_content_hash, model="opus-4-7", real cost data) — verified via integration test: entry updated in place by drive_file_id (exactly one match confirmed by grep).
- Prior text-mode Lecture 4 entry: overwritten in place (not duplicated). Confirmed.

---

## Auto-detect heuristic (Day 2 Part 2)

### What was built

`pdf_mode_detector.py` — classifies a PDF as "text" or "image" based on pypdf-extracted text quality. Returns a dict: `mode`, `recognizability`, `max_garbage_run`, `total_tokens`, `reason`.

### Design philosophy

Bias hard toward image mode. Text-mode on handwritten content silently fabricates (the L4 failure); image-mode on typed content just costs more tokens. Image mode is the safer failure direction.

### Heuristic logic (locked)

A token is recognized if it: contains Hebrew (U+0590–U+05FF), matches a Latin word `[A-Za-z]{2,}`, is a number, is a single math symbol from a small whitelist, OR consists entirely of math-italic characters (every char in U+1D400–U+1D7FF).

- `recognizability < 0.85` → image
- `max_garbage_run > 20` contiguous unrecognized tokens → image
- Otherwise → text

### Calibration journey (3 iterations against real PDFs)

**Iteration 1 (initial whitelist):** L9 (typed) misclassified as image at 82% — failed because pypdf renders math fonts as Mathematical Italic Unicode chars (𝑆, 𝑢, 𝑗𝜔) that the Latin `{2,}` rule rejected.

**Iteration 2 (added math-italic with length cap ≤8):** L9 jumped to 92% ✓ but L4 (handwritten) regressed to 88% — short compound tokens like 𝑓(𝑡), 𝑎→0+, 𝑎2+𝜔2 (L4's dominant pattern) passed the cap. Length cap can't distinguish 𝑓(𝑡) from dist(𝑢) structurally.

**Iteration 3 (pure-math-italic rule, regex `^[\U0001D400-\U0001D7FF]+$`):** Only tokens where every character is math-italic count. Isolated variables (𝑆, 𝑢, 𝑗𝜔) recognized; anything mixed with parens, digits, ASCII, or operators rejected.

### Final real-file results

| File | Mode | Recognizability | Margin |
|------|------|-----------------|--------|
| Linear Systems L4 (handwritten) | image ✓ | 80% | 5 points below threshold |
| Design of Algorithms L9 (typed) | text ✓ | 87% | 2 points above threshold |
| Complex Functions Tutorial 3 | image | 68% | well clear |

### Tests

15/15 unit tests pass.

### Calibration verdict

Thresholds locked at 0.85 recognizability and 20-token garbage run. L9's 2-point margin is thinner than ideal — a formula-heavier typed lecture could land close to the threshold. Accepted because:
- Image mode on a borderline text PDF is the safer failure direction
- Real-run metrics (logged per file in `translated_log.json` on integration) will give actual data for retuning later
- Tightening further would require per-page variance analysis (significantly more code, diminishing returns)

### Monitoring plan

After 10–15 real runs through `translate_smart.py`, review the `recognizability` and `max_garbage_run` distributions logged in `translated_log.json`. Retune 0.85 / 20 thresholds if borderline files are misclassifying. If retuning alone isn't enough, add per-page variance check (typed-with-handwritten-body PDFs show high page-level variance; uniform typed PDFs don't).

### Integration test results

| Test | Result | Notes |
|------|--------|-------|
| 1 — known-done (DoA L7) | already done, zero API calls ✓ | exited immediately after hash match |
| 2 — L4 retranslate | image mode, 111s, $1.52 ✓ | 1 log entry, updated in place; saved to Lectures/ subfolder (fixes old bad path) |
| 3 — DoA L8 (new) | text mode, 72s, $0.61 ✓ | 89% recognizable, entry created in log |

L4 entry updated in place by drive_file_id (verified via grep — exactly one match). Confirms manifest update semantics for the agent loop.

Recognizability margins on real text-mode files so far: L9=87%, L8=89%. Both comfortably above the 0.85 threshold. n=2 — not enough to retune, keep collecting.

---

## Decisions made

### Routing strategy: semantic, not lookup-based
**Old plan**: `subfolder_types.json` mapping Hebrew subfolder names → role.
**New plan**: drop the mapping file entirely. Opus 4.7 reads Hebrew folder/file names and classifies semantically.

**Rules**:
- Skip wins (any segment matching פתור/פתרון/תשובות → skip)
- Inherit when unclear
- Course name from top-level folder, looked up in courses.json
- `ask_user` when genuinely uncertain
- Every routing decision logged with one-line reasoning

### Mapping & state files
- `courses.json` — Hebrew → English course name. Source of truth. Agent never writes without ask_user approval.
- `subfolder_types.json` — eliminated.
- `translated_log.json` — state manifest. Hash-based dedup.

### Token limits
- `max_tokens=16000` for translate_one.py
- Headroom for 10–15 page dense lectures

### Cost projection
- ~$0.67 per text-PDF lecture
- ~60 lectures/semester × $0.67 = **~$40/semester**

### save_to_vault path model: executor + escape hatch (not free-form path choice)
**Question raised:** Should the agent choose the save path dynamically from filename/
content, rather than a function deciding it?
**Decision:** Keep vault_output_path as the EXECUTOR of a decision the agent already 
made, with a custom-subfolder escape hatch for non-standard files.
**Rationale:**
- The dynamic, content-aware path decision the instinct wanted ALREADY happens — at 
  routing/classification time, where the agent reads Hebrew names + content and assigns 
  course + type (lecture/tutorial/homework/exam). vault_output_path doesn't re-decide; 
  it spells the path from that decision.
- Moving path choice to save time would DUPLICATE the type decision into two places 
  that can disagree (agent classifies "homework", save logic independently picks 
  "Exams/").
- TYPE_TO_FOLDER stays a fixed map on purpose: guarantees ONE canonical folder name 
  ("Lectures/" always, never Lectures/lectures/Lecture casing variants) across ~60 
  files/semester. Obsidian treats casing variants as separate folders — the constant 
  prevents fragmentation that free-form reasoning produces.
- Ties back to the L4 stale-path bug (file saved to course root instead of Lectures/) 
  and Phase 3 cloud safety (vault-relative, no absolute paths).
**Escape hatch (the part the dynamic instinct got right):** files that genuinely fit 
none of the four standard types (formula/equation sheet, syllabus, general reference) 
— the agent passes custom_subfolder="<name>" and vault_output_path uses it verbatim. 
Unknown type WITHOUT custom_subfolder still raises ValueError (a garbled type is a real 
error, not license to guess).
**Soft guard:** custom subfolder names should stay consistent across runs (reuse 
"Reference/" rather than invent "References/"). A constant can't enforce this for 
non-standard types — it lives as a prompt instruction. If a custom folder gets used 
often, promote it into TYPE_TO_FOLDER as a new standard type.
**Follow-up owed:** agent_routing_prompt.md must tell the agent the escape hatch exists 
(a file fitting none of the four types may use a custom subfolder), or the hatch is code 
the agent never reach

---

## Prompt issues for Step 0.4 batch revision (locked-in list)

Single revision pass. See `prompt_revision_proposal_v2.md` (this session) for proposed text changes.

1. **Conversational preamble** — response must begin directly with YAML *(Day 1, not yet recurred)*
2. **Fabricated `date_translated`** — strengthen to: omit field entirely if today's date not known with certainty *(recurred in both files; recurred in Lecture 4 image-mode redo despite revision — needs code-level date injection, not stronger prompt)*
3. **Filename wins over document header** *(Day 1, not yet stress-tested)*
4. **Intermediate / redo output** — final version only *(Day 1, not yet recurred)*
5. **Figure descriptions weak** for graph diagrams *(Phase 2 fix, not a prompt fix)*
6. ⭐ **YAML code-fence wrapping** *(both files — explicit example needed)*
7. ⚠️ ~~**Reconstruction on heavy-OCR-garbage sources**~~ — **PARTIALLY RESOLVED** — refuse-rather-than-reconstruct rule added (text-mode Lecture 13 clean). Blank-box example still pending — image-mode Lecture 4 filled a derivable empty $Z_1(\omega)$ box. Add example to prompt: "If the source contains a blank box, empty field, or placeholder, leave it blank — even if derivable from context. Mark as `[blank in source]`." *(Lecture 4)*
8. ⭐ **Don't invent figure descriptions** — placeholder only when figure not visible *(Lecture 4)*
9. ⭐ **No markdown syntax in prose** *(Tutorial 3, minor)*
10. ⭐ **Don't promote section headings to theorem labels** *(Tutorial 3, minor)*

---

## Open questions / things to revisit

- Image-mode vision for figure-heavy pages even when text extraction works (Phase 2)
- Cost ceiling per run — define a hard $ cap separate from tool-call cap once costs are real
- `translated_log.json` schema additions for Phase 2: `skipped_transient`, `skip_source`, `error_message`
- Should Phase 3 cloud agent commit `translated_log.json` updates back to repo? (yes — confirm in Phase 3 design)

---

## Architectural pivot: agent-driven mode dispatch + skills

### Trigger

Translated four Complex Functions homework PDFs through `translate_smart.py`. All four are typed PDFs, math-heavy, zero handwriting. The auto-detect heuristic landed every one at ~60% recognizability and dispatched them to image mode — significant cost overhead versus the text-mode path they should have taken.

Root cause: pypdf renders LaTeX/math fonts as fragmented Mathematical Italic Unicode tokens (𝑓(𝑧), 𝑎+𝑏𝑖, →0+). The Iteration-3 pure-math-italic rule rejects these the moment they're mixed with parens, digits, or operators — which is what real math looks like. The 0.85 recognizability threshold and 20-token garbage-run cap fit the calibration set (L4 handwritten, L8/L9 typed-light-math) but cannot structurally distinguish fragmented LaTeX from OCR garbage on formula-heavy typed content. Calibration verdict in Day 2 Part 2 already flagged this risk; the Complex Functions batch confirmed it.

### Diagnosis

Hard-coding mode-dispatch parameters fights the agentic character of the system. The agent should look at the evidence and decide, the same way Claude in chat decides how to handle an uploaded PDF without being told. Execution craft (DPI choice, prompt emphasis, vault path conventions) belongs in skills loaded by tools at invocation time — not in the agent loop's instructions.

### Decision

Replace threshold-based dispatch with agent reasoning over signals. Move execution craft into per-tool skills.

- `pdf_mode_detector.py` returns **signals only** (recognizability, max_garbage_run, math-italic counts, preview text, unrecognized_sample, page_count, file_size_kb). No `mode` verdict. No threshold gates.
- `translate_smart.py` deleted. Mode dispatch IS the tool call — the agent picks `translate_text_pdf` or `translate_image_pdf` based on what `read_file` returned.
- Skills encode per-tool execution craft (LaTeX preservation rules, DPI, prompt overrides, vault path map, blank-box handling), loaded by each tool at invocation.

### Three sub-decisions locked this session

1. Skills live at `<repo_root>/skills/<skill-name>/SKILL.md` — next to code, shipped with the repo.
2. Each translation tool loads its skill **every call** (per-invocation, not per-startup) — keeps tool code thin and lets skill content evolve without restarts.
3. Mode ambiguity defaults to image mode with **no `ask_user`**. Image is the safer failure direction (per the L4 finding — text-mode silently fabricates on handwritten content; image-mode on typed content just costs more). `ask_user` is reserved for routing/naming uncertainty.

### What the calibration work bought us

The three heuristic iterations weren't wasted. They surfaced the typed-vs-handwritten boundary, identified the safer failure direction, and produced the signals — recognizability, max_garbage_run, math-italic rule — that the agent will now reason over. Calibrated thresholds become reference notes the agent can read, not running gates.

### Manifest schema additions

- `chosen_mode`: `"text"` | `"image"` — which translation tool was called
- `mode_reasoning`: one-line string explaining the choice (e.g., "recognizability 62% but unrecognized tokens are fragmented LaTeX not OCR garbage — text mode")

Backfilled for existing entries from known data (L4 = image, L8 = text, L9 = text, etc.).

### Tool set revised

Agent now has **7 tools** instead of 6: `list_folder`, `read_file`, `translate_text_pdf`, `translate_image_pdf`, `save_to_vault`, `ask_user`, `update_mapping`. The implicit dispatcher (`translate_file` → mode param) is gone; text and image translation are separate tools.

---

## Model migration: Opus 4.7 → Opus 4.8 (2026-05-28)

Switching all agent translation work from `claude-opus-4-7` to `claude-opus-4-8`. Project custom-instructions rule ("Opus 4.7 for ALL agent translation work") supersedes to 4.8.

### Scope of change

- `translation_engine.py` model string: `claude-opus-4-7` → `claude-opus-4-8`
- Any other call sites (`translate_one.py`, `translate_image_pdf.py`, agent loop when built) — grep before patching to catch all.
- `translated_log.json` `model` field: future entries write `"opus-4-8"`. Prior entries (`"opus-4-7"`, `"manual"`) left untouched — they're historical record.
- Cost projection (~$40/semester) assumed Opus 4.7 pricing. Revise once 3–4 files run on 4.8 — pricing may differ.

### Calibration baseline carryover

Prompt issues list, mode-detector signals, blank-box deferral all carry over unchanged — they're about content faithfulness, not model version. Re-run one text-mode (DoA-style) and one image-mode (Linear Systems L4-style) file on 4.8 before declaring the swap clean. Watch specifically for:

- Issue 2 (fabricated `date_translated`) — code-level injection should still hold, but verify on first image-mode run.
- Issue 7 (blank-box filling) — deferred regression; check whether 4.8 changes behavior either direction.
- Vision-mode preamble override in `translate_image_pdf.py` — may need re-tuning if 4.8 has different default verbosity.

### Risk

Calibrated thresholds (0.85 recognizability, 20-token garbage run) are pypdf-output-based, not model-based — unaffected by the swap. Mode-dispatch refactor (architectural pivot above) is independent of model version; both efforts can proceed in parallel.

---

## Day 2 redesign prework (2026-06-01)

### 1. Opus 4.8 migration + pricing correction

Migrated to `claude-opus-4-8`. Found the cost code used $15/$75 per M, which is Opus 4.1/4 pricing — wrong even for 4.7 ($5/$25 has held since Opus 4.5). Logged costs were ~3x inflated (e.g. the ~$0.67 reference lecture was really ~$0.22; per-semester projection ~$40 → ~$13). 4.7→4.8 is config-only, no API breaking changes. Do not use fast mode (2x cost, no benefit for background runs).

### 2. Skill-injection redesign adopted (from earlier 2026-05-21 design)

6→7 tools (`translate_file` split into `translate_text_pdf` + `translate_image_pdf`). Detector stops returning a verdict; the agent decides mode from raw signals — no threshold dispatcher in the middle.

### 3. Prompt/skill architecture

One shared skill + thin mode-skills (not fat self-contained skills), chosen for DRY/anti-drift. Flat skill filenames (no folder/SKILL.md) because our tools read explicit paths — nothing auto-discovers. Caveat: real Anthropic-discovered skills would require folder/SKILL.md. `agent_routing_prompt.md` is deliberately NOT a skill — different audience (the loop, not a translation call).

### 4. Field naming locked

`chosen_mode` / `mode_reasoning` (not `detection_*`). Manifest migrated accordingly.

### 5. Detector signal design (validated against a 13-PDF corpus)

Signals: `recognizability`, `tokens_per_page` (strongest yield signal — isolates raster/handwritten), `bytes_per_token` (scanned-page tell), `math_token_fraction` (resolves formula-sheet-vs-lecture: math inline in prose → text, math dominating the page → image), `per_page[]` (exposes typed-scaffold-over-handwritten hybrids that doc-level averages hide), `unrecognized_sample` (agent reads it to split the unsplittable ~56–99 tok/page band).

Finding: `max_garbage_run` fires only on TYPED RTL+math files, never handwritten — so it's an extraction-quality signal, NOT a handwriting detector; relabeled `max_garbage_run_DIAGNOSTIC`, diagnostic-only. `wrong_pointing_object` warnings = confirmed noise (Malam sheet had 0 warnings yet is most extraction-hostile), excluded as a signal. Corpus showed 12/13 route to image — recognizability alone is barely a detector; the multi-signal set + agent reasoning is what discriminates.

### 6. No rich-signal backfill of old manifest entries

Fabricating detector signals for files the detector never saw violates refuse-rather-than-reconstruct. Old entries keep honest nulls; rich signals populate going forward on real runs.

### 7. Code-organization decision

The two translate functions stay in one module (`translation_engine.py`), not split per-tool — they're ~80% identical; splitting would need a third shared file for no gain. Revisit only if the image path grows a real subsystem (Phase 2 hybrid mode).

### 8. Manifest I/O centralized into manifest.py

`translated_log.json` read/write was duplicated across three scripts and about to be needed by `save_to_vault` (writes) and `read_file` (reads, for dedup). Consolidated into one module so the manifest's rules live in one place. Key behavior fixed here: upsert is now IN PLACE (match by `drive_file_id`, replace at same index) — the old inline script versions did remove-then-append, which reordered entries and produced noisy git diffs on the committed manifest. `find_by_id` added now (`read_file` will need it). Drive helpers deliberately NOT moved — they belong in a future `drive.py`, out of scope.

### 9. Function vs tool-layer distinction (clarifies how Part 4 tools are structured)

A "tool" is two layers: the underlying FUNCTION (pure logic) and the TOOL WRAPPER (Anthropic schema + dispatch + handler). Decision: the wrapper layer for ALL seven tools lives together in `agent.py` (the loop is the only thing doing tool-use); the underlying functions live by concern (`translate_*` + `save_to_vault` in the engine, manifest ops in `manifest.py`, future Drive ops in `drive.py`). A function being in the engine does not make it "not a tool" — tool-ness is the schema wrapper added in the loop. Building a function before its schema locks in nothing; re-homing a function pre-`agent.py` is a one-line import change. Final module layout for the tool functions is a 4.1 decision (once all 7 contracts are pinned), not forced now.

---

## Day 2 Part 4.1 — tool spec locked (2026-06-01)

### ask_user dropped; Phase 3 contract adopted early (B)

`ask_user` (blocking `input()`) would be rewritten for cloud anyway (Phase 3.5 makes it skip+log). Adopted the non-blocking contract NOW, run it locally. On uncertainty the agent records a pending entry and continues — no blocking. Approval happens out-of-band in a review step, not mid-loop. Cost: unmapped courses take two runs (discover → approve → translate). Acceptable — new courses are rare.

### Tool count 7 → 6

`ask_user` replaced by `flag_for_approval` (a thin manifest-write tool, one job). `update_mapping` EXITS the loop entirely — imported only by `review_pending.py` (not yet built). Enforcement that `update_mapping` can't fire mid-loop = it has no tool schema in the loop. No runtime guard needed.

Loop tools (6): `list_folder`, `read_file`, `translate_text_pdf`, `translate_image_pdf`, `save_to_vault`, `flag_for_approval`.
Outside loop: `update_mapping` (review path only).

### Cache@hash — payloads never cross message history

Tool results are strings in the loop's message history. Full PDF text (~8K tok) and base64 images (~50K tok) must NOT go there — bloats context and re-bills every subsequent turn. Decision: `read_file` caches downloaded bytes; translate tools read from cache; translated `.md` also goes to cache. Cache is keyed by `source_hash` (NOT `file_id` — a re-edited source must miss stale cache). Cache is IN-MEMORY, run-scoped (dies with the run; crash → re-run is cheap; no cache-invalidation logic, no gitignore surface). The agent only ever sees signals (small) + handles, never content.

### read_file does hash comparison, not just id lookup

`find_by_id` matches by `drive_file_id`; dedup is hash-based (catches source re-edits). `read_file` compares live sha256 against stored. Branches:
- id match + hash match + `md_path` non-null → `already_done`
- id match + hash match + `skipped_permanent` → `already_done`
- id match + hash match + `pending_approval` → `already_pending` (don't re-flag)
- id match + hash match + `not_translated_yet` → proceed
- no id match OR hash differs → proceed

### 6-tool contracts (the 4.1 spec)

`list_folder(folder_id)`
- out: JSON list of `{name, id, type:"file"|"folder", mime_type}`. manifest: none. Eyes only, no dedup.

`read_file(file_id)`
- does: download → sha256 → `manifest.find_by_id` → hash compare → run detector
- out (ready): `{status:"ready", source_hash, page_count, file_size_kb, signals:{recognizability, tokens_per_page, bytes_per_token, math_token_fraction, per_page[], unrecognized_sample, max_garbage_run_DIAGNOSTIC}}`
- bytes → cache@hash, NOT returned. manifest: READ. Dedup branches above.

`translate_text_pdf(source_hash, course, drive_filename, mode_reasoning)`
- reads cache@hash, pypdf text, shared+text skill, Opus 4.8.
- out: `{status:"translated", md_cache_handle, chosen_mode:"text", cost_data:{input_tokens, output_tokens, cost_usd, model:"opus-4-8"}}`
- refusal backstop: heavy OCR garbage → `{status:"refused", reason}` → agent reconsiders (image, or flag). manifest: none.

`translate_image_pdf(source_hash, course, drive_filename, mode_reasoning)`
- same shape, `chosen_mode:"image"`. pdf2image @ 200 DPI, base64 vision, code-level date injection (Issue 2 fix), image skill.

`save_to_vault(course, filename, file_type, source_hash, md_cache_handle, drive_file_id, drive_filename, cost_data, chosen_mode, mode_reasoning, signals=None, custom_subfolder=None)`  [BUILT]
- reads md from cache, atomic `.md` write, `manifest.upsert_entry` in place, stamps `chosen_mode`/`mode_reasoning`, logs signals when provided, model from `cost_data`. out: vault-relative path. manifest: WRITE (sole writer for real translations). Unknown `file_type` w/o `custom_subfolder` → `ValueError`.

`flag_for_approval(drive_file_id, drive_filename, source_hash, kind, reasoning, proposed_english=None, proposed_type=None)`
- `kind`: `"course_mapping"` | `"routing"`. Writes manifest entry `model:"pending_approval"`, `md_path:null`, `pending:{kind, hebrew_name, proposed_english, proposed_type, reasoning}`. out: confirmation string. No translate, no `update_mapping`, loop continues. manifest: WRITE.

### Loop invariants

All tool results JSON-encoded strings. 200-call budget tracked, break+warn on exceed. `logs/agent_<ts>.log` per run (tool, inputs, truncated outputs, reasoning). Skip-wins routing in prompt. Content/md never in history — only signals + handles.

### Owed (decisions made, not yet built)

- `pending_approval` model value + `pending{}` block — manifest migration lands when `flag_for_approval` is built.
- `review_pending.py` — the resolution path (lists pending entries, takes decision, fires `update_mapping` on approval, flips status to `not_translated_yet` so next run picks it up). Phase 1 deliverable.
- `agent_routing_prompt.md` must carry, before 4.2 runs usefully: routing policy (skip-wins, type classification, inherit), `flag_for_approval`-vs-proceed criteria (both-directions examples), MODE-FROM-SIGNALS guidance (how to weigh `recognizability` / `tokens_per_page` / `math_token_fraction`; image is safer default on ambiguity — this is new post-architectural-pivot and most likely missing), termination ("done with root folder → end run"), dedup awareness (don't re-translate `already_done` / re-flag `already_pending`). Tool mechanics stay in the schemas, NOT the prompt.

---

## Day 2 Part 4 — full agent autonomy on naming + routing (2026-06-02)

### Decision: agent decides course naming AND type/routing with NO approval gate

REVERSES two prior locks:
- "agent never writes mappings (`courses.json`) without explicit user approval"
- the architecture-B / `flag_for_approval` pending-approval flow adopted last session

New stance: the agent autonomously decides course English names, file types (lecture/tutorial/homework/exam), routing, custom subfolders, and folder creation. No `flag_for_approval`, no pending entries, no out-of-band review step. This is the maximal version of the agentic bet the whole project rests on (semantic routing, "agent decides not lookup tables") — flagging was the last place we held back; removing it for consistency.

Explicitly a BET. If naming/routing drift turns out wrong in practice, tighten back toward a gate. The audit log (below) is what makes the bet reversible — we'll SEE wrong decisions instead of discovering them by accident.

### What this removes

- `flag_for_approval` — DELETED (both kinds: `course_mapping` and `routing`).
- Architecture B and its machinery — DROPPED. B existed only to make approval non-blocking; with no approval there's nothing to defer.
  - `review_pending.py` — NO LONGER a Phase 1 deliverable.
  - `pending_approval` manifest state + `pending{}` block — NOT needed, don't build.
- `ask_user` — already gone (replaced by flag earlier); stays gone.

### What this adds / changes

- `update_mapping` RETURNS to the loop as a tool. When the agent names a new course it writes `courses.json` so later runs stay consistent. (Was evicted to review-path-only last session; now back in-loop.) The "never writes without approval" guard is intentionally removed.
- Decision A (`courses.json` injected into the kickoff message) STILL STANDS — the agent reads existing approved mappings for spelling consistency; absent → it auto-names AND writes via `update_mapping` (no longer flags).

### MUST-HAVE replacement for the lost visibility: loud decision logging

Flagging WAS the visibility mechanism. Removing it without a replacement = silent fabrication risk (won't know the agent guessed wrong until a misfiled/mis-named file surfaces weeks later). Replacement: every AUTONOMOUS decision logged prominently in the per-run log (the Stage-3 `logs/agent_<ts>.log`), not buried:

- new course auto-named → log line + manifest marker (e.g. `auto_named: true`): "אלגוריתמים → 'Algorithms' (no prior mapping, agent-assigned)"
- non-obvious routing → "2023.pdf → exam (inherited from מבחנים parent)"
- `custom_subfolder` invented → logged

Post-run, one scannable list = "everything the agent decided on its own this run." This is the audit surface that makes the bet reversible.

### The honesty floor (NOT a gate — keep it)

Distinct from "uncertain but made a reasonable call" (fine — log, proceed). For GENUINE "cannot determine" (gibberish folder name, unclassifiable file, unreadable course name): the agent SKIPS + logs as unprocessed — does NOT invent. Same principle as the locked refuse-rather-than-reconstruct guardrail for handwritten PDFs: when it can't READ → refuse; now also when it can't CLASSIFY → skip, don't misfile. Not blocking, not pending — just an honest "I couldn't handle this, it's in the skipped list, look yourself."

### Revised tool set (6, different from last session's 6)

`list_folder`, `read_file`, `translate_text_pdf`, `translate_image_pdf`, `save_to_vault`, `update_mapping`.
(`flag_for_approval` removed; `update_mapping` back in.)

### Coupling noted (watch for it)

Naming-uncertainty and routing-uncertainty often co-occur — a folder you can't name is often one you can't structurally read either. Trusting auto-naming partly means trusting auto-classification. The skip-floor is the backstop so the agent has somewhere honest to put true "I don't know what this is" instead of guessing at everything.

### Owed / not yet built (from this decision)

- `update_mapping` in-loop contract (writes `courses.json`; the old "requires prior approval" guard is GONE).
- decision-log shape: which fields, where (per-run log + manifest `auto_named` marker).
- skip-floor behavior: how the agent signals "cannot determine" and how it's logged as unprocessed.
- `agent_routing_prompt.md` needs another edit: remove `flag_for_approval` references, tell the agent it MAY auto-name (write via `update_mapping`) and MAY route autonomously, but MUST skip+log genuine cannot-determine cases rather than guess. (Prior edits referenced `flag_for_approval` — those lines are now stale again.)

---

## Day 2 Part 4.3 — handlers built: list_folder, read_file, translate pair (2026-06-02)

### Decision A landed: `courses.json` injected into kickoff (not a tool)

The agent reads approved course mappings from the kickoff message, not via a tool call. `courses.json` (flat `{hebrew: english}`) is loaded at startup, formatted as a `- <he> → <en>` block (`ensure_ascii=False`), prepended to the kickoff user message. Rationale: the agent classifies folder/file ROLES semantically itself; `courses.json` only supplies the approved English SPELLING (consistency across ~60 files/semester — Obsidian fragments on casing variants) and, post-reversal, the auto-name baseline. In-list → use as-is. Absent → auto-name + persist via `update_mapping` + log (per the autonomy reversal — no longer flags). Prompt rule reworded to point at the injected block, not a readable file.

### `list_folder` (handler #1) — built

Function `list_folder_children(folder_id)` in `drive.py`: module-level Drive service built once at import (no re-auth mid-run), direct children only (NOT recursive — the agent walks the tree via repeated calls; this is where skip-wins lives), paginates on `nextPageToken` (`pageSize 100` — a 100+ file folder must not silently truncate), returns `{name, id, type, mime_type}` with type derived from `mimeType`. Thin handler in `agent.py` `json.dumps(..., ensure_ascii=False)`. Verified: 25 real root children, Hebrew readable, types correct, no truncation.

### `read_file` (handler #2) — built

- `CONTENT_CACHE`: module-level dict in `agent.py` (loop scope), keyed by `source_hash` NOT `file_id` (a re-edited source must miss stale cache). Run-scoped, in-memory, dies with the run.
- `read_file_logic`: download bytes (`drive.download_bytes`, reuses service) → hash → `manifest.find_by_id` → dedup branches → `detect_pdf_mode` → cache bytes → return signals only (bytes NEVER in the return).
- CRITICAL CATCH: stored `source_content_hash` carries a `"sha256:"` prefix. A bare hexdigest would never match → silent re-translation of the entire corpus (discovered as a surprise bill, not an error). Added canonical `manifest.sha256_of(data) = "sha256:"+hexdigest()`, homed in `manifest.py` because the manifest owns the hash contract. Agent passes the full `"sha256:..."` string as `source_hash`; cache keys line up directly.
- Dedup branches (4, not 5): `md_path` non-null → `already_done`; `skipped_permanent` → `already_done`; `not_translated_yet` → proceed; no-match-or-hash-differs → proceed. The `already_pending`/`pending_approval` branch is DELETED — orphaned by the autonomy reversal (`flag_for_approval` gone, nothing writes that status). Also stripped `already_pending` from `read_file`'s schema description.
- All failures (download/detector) → `{"status":"error","reason":...}` string, never crash the loop.
- Verified: 6 dedup/error branches pass; real integration — `already_done` short-circuits with zero cache writes; a `not_translated_yet` file downloads → detects → caches 10.5 MB by `source_hash` with zero bytes leaked to the return.

### `translate_text_pdf` + `translate_image_pdf` (handler #3) — built

- Shared `_translate_logic` core (paths near-identical; engine stays one module per locked decision), two thin handlers.
- Reads bytes from `CONTENT_CACHE[source_hash]`, calls existing `translation_engine` functions as-is (NOT rewritten), caches translated md under `f"{source_hash}:md"`, returns the HANDLE not the content (same no-content-in-history rule as bytes). `save_to_vault` will read that handle.
- `today_date` computed in handler (`datetime.now(timezone.utc)`), passed to engine → injected into prompt (Issue-2 date-fabrication fix). Verified image path `date_translated` = real today, not fabricated.
- `cost_data`: real tokens from `response.usage`. Confirmed engine `_calc_cost` uses CORRECTED $5/M in / $25/M out (NOT the old 3x-inflated $15/$75). `model` string is canonical `"claude-opus-4-8"` (matches manifest entries — not the short `"opus-4-8"`).
- First clean per-file cost data at corrected pricing: text $0.302 (14559/9172 tok), image $0.338 (22511/9004 tok). Per-semester ~$13 projection now has real per-file backing.
- Cache-miss → `{"status":"error", "reason":"...read_file must run first"}`, no crash.

### Refuse-rather-than-reconstruct hardened to STRUCTURAL (the L4 guardrail)

Problem found: the project's highest-priority guardrail (don't reconstruct unreadable content from priors — the L4 failure) was only partly structural. Extraction-yield refusal (<50 chars → engine `RuntimeError` → `status:"refused"`) was structural and agent-visible. But CONTENT-LEVEL refusal (text extracts >50 chars but is OCR garbage the model would reconstruct) was model-side only — the model wrote a refusal in the markdown PROSE, returned `status:"translated"`, and the agent treated it as success. Under the autonomy reversal there's no approval gate to catch a buried refusal → silent fiction could land in the vault. This is exactly the L4 failure mode.

Fix (structural marker):
- `skills/translate-shared.md`: on content it cannot faithfully translate, the model emits `"REFUSED: <reason>"` as the VERY FIRST LINE and nothing else — no frontmatter, no partial, no reconstruction.
- translate handlers: after the engine returns, before caching, check if output starts with `"REFUSED:"` → return `{"status":"refused", "reason":...}`, cache nothing, no handle. First-line-only check (a body mention of refusal/garbage topics won't false-trip).
- `agent_routing_prompt.md`: a translate tool may return `status:"refused"`; on text-mode refusal reconsider image mode; if image also refuses → skip + log as unprocessed (skip-floor), never force a translation.

Guardrail's real shape now: detector is the PRIMARY defense (keeps handwritten/garbage out of text mode in the first place); structural `REFUSED:` marker is the backstop when garbage reaches a translate call anyway; BOTH extraction-yield and content-garbage refusals surface as `status:"refused"`; agent reacts image-retry → skip-floor. Verified: garbage source → `REFUSED:` → `status:"refused"`, nothing cached; clean file → translated unchanged; first-line check doesn't false-trip on refusal-topic content.

### Skip-floor = run-log only, no manifest state (decision)

Genuine cannot-determine cases (gibberish folder name, unclassifiable file, image-mode-also-refused source) → logged in the per-run log, NO manifest entry. Consequence accepted: such files are re-encountered + re-skipped every run (no manifest record to short-circuit them). This is acceptable at ~60 files/semester AND self-healing — fix the source/name between runs and the file classifies + gets picked up automatically, no manifest cleanup. NOT a new model value; `skipped_permanent` stays "deliberate skip" (e.g. פתור). Skip-floor is the agent's reasoning text + the run logger, not a tool or a manifest write.

---

## Day 2 Part 5 → Phase 1 close-out: tools finished, the truncation saga, the $60 post-mortem, caching, dedup, md5 gate (2026-06-04)

This is the long arc from "3 of 6 handlers, crashes at `save_to_vault`" to **Phase 1 COMPLETE** — every tool built, the corpus translated, and the cost model finally understood and instrumented.

### Stage 3 landed: budget guard + run logger + crash-safe summary

`save_to_vault` and `update_mapping` were wired, closing the 6 core handlers, and Stage 3's guards went in around the loop:
- **200 tool-call budget** — hard cap; the loop stops and summarizes rather than burning credits on a runaway walk.
- **Run logger** — the loud autonomous-decision log (every auto-name + route decision with the agent's reasoning) plus skip-floor logging, since the autonomy reversal removed the approval gate that used to make those decisions visible.
- **Crash-safe end-of-run summary** — the summary is emitted even when the run dies partway, so a crash still tells you what landed and what didn't.

### The truncation saga (the expensive bug, found by audit not by error)

A nasty silent-corruption chain, in order:
1. **BrokenPipe** on large downloads/translations surfaced first as a crash.
2. Fixing the crash exposed the real problem underneath: **silent truncation** — a translation could come back short (cut off) with `status:"translated"` and no error. It looked successful.
3. **Key finding: `num_retries` does NOT catch truncation.** The SDK's retry only fires on transport/HTTP errors; a response that completes but is short is "success" to it. The fix is an **explicit length-verify loop** — verify the output is complete (not stop-reason-truncated / not implausibly short for the page count) and re-issue if not.
4. **Corpus audit** — once the integrity check existed, ran it across everything already translated to find prior casualties.
5. **One casualty: Lecture 13.** Exactly one file had landed truncated before the fix; re-translated.

Lesson: truncation is invisible to the obvious guard (retries). Only an explicit completeness check catches it, and only an audit finds the ones that already slipped through.

### The $60 post-mortem + prompt caching VERIFIED

A surprise **~$60** spend forced a real cost breakdown. Cause: **uncached × ~5 re-runs × growing context** — every re-walk re-paid full input cost on an ever-larger history.
- Split: **~$21 translation** vs **~$39 routing**. The routing half — the agent walking the tree, reading folders, deciding — was the larger and previously *invisible* cost.
- **Prompt caching VERIFIED live** afterward: `cache_read` token counts climb turn over turn (direct evidence the cache is hit), driving steady-state input to ~2 tokens/turn.
- **TTL finding:** the default cache TTL is **5 minutes**, which expires mid-run on slow image-heavy sweeps. Use `ttl:"1h"` ONLY for big/image-heavy sweeps where a turn can exceed 5 min; the default 5-min TTL is fine for normal text runs.

### Cross-ID SHA dedup

Dedup now keys on **source content SHA across Drive file IDs** — the same bytes re-uploaded under a new Drive ID (a common real-world case) are recognized as already-translated instead of re-paying. Complements the existing per-ID manifest lookup.

### Phase 1 COMPLETE

All of Semester ד translated — **~97 files**. Full loop: autonomous naming + routing, budget-guarded, audit-logged, crash-resilient, caching verified. This is the Phase 1 goal met.

### Save-immediately rule (the soft side of Gap 3)

Rule added to `agent_routing_prompt.md`: on a successful translate, **save in the same step** rather than batching saves to the end. This is the *soft* guarantee against losing a translated-but-unsaved file to a crash. The *hard* guarantee (disk-persisted cache) remains open — see PHASE2_NOTES #3.

### Context-offloading (a learning exercise that taught the opposite of its premise)

Verbose detector signals (`per_page` arrays, `unrecognized_sample`) were moved behind handles, retrievable on demand via a new tool `fetch_signal_detail`, to keep them out of message history. Outcome: **`fetch_signal_detail` got 0 calls.** The agent never needed the verbose detail to pick a mode — the summary signals (`tokens_per_page`, `math_token_fraction`, recognizability) carry the decision. Useful finding: the verbose signals are **not load-bearing for mode choice**, which is itself an argument for dropping the tool (queued as an open optimization question).

### md5 freshness gate + verified backfill

New optimization: before downloading a file's bytes, compare Drive's `md5Checksum` against the stored `source_md5`; if equal, **skip the byte download entirely** (the source is unchanged). Chosen over `modifiedTime` because **`modifiedTime` is unreliable on a synced folder** — sync churn bumps it without a content change. The md5 is sync-immune.
- **Backfilled 97/98** entries with `source_md5`. The audit during backfill found **0 new stale entries** and the **one 404 orphan** (source gone from Drive). The orphan was **left in place** — harmless, costs nothing.
- This backfill run doubled as a one-off execution of the standing manifest-integrity audit idea (PHASE2_NOTES #6).

### Resume-strategy decision: no auto-restart (by policy)

Decided NOT to build auto-restart-after-crash. Rationale: caching dropped re-walk cost ~90%, so a manual re-run from the top is cheap; **manual `--folder` scoping** handles "just redo this part"; a mid-run **checkpoint is deferred, likely permanently** (the bookkeeping cost isn't justified now that re-walks are cheap). This is a deliberate policy choice, not an unbuilt feature.

### Meta-lesson

The real failure wasn't any single bug — it was that **routing cost was uninstrumented.** The translation half was always measured (per-file `cost_data` in the manifest); the routing half wasn't, so ~$39 of re-walk cost accumulated invisibly across re-runs before anyone could see it. The fix isn't a patch, it's **visibility**: per-turn `cache_read`/`cache_write` logging now closes that blind spot. (Folding those per-turn numbers into a single end-of-run total is the remaining open piece — PHASE2_NOTES #8.)

---

## Optimization rejected: merge `translate_*` + `save_to_vault` (2026-06-05)

Examined and rejected. `save_to_vault` is the sole manifest writer; merging translate into it would either duplicate that responsibility into both translate tools or create one fat tool doing translate + disk write + manifest write — three failure modes in one round-trip with no checkpoint between. The save-immediately rule already delivers the coupling benefit (shrunk translated-but-unsaved crash window) without collapsing the tool boundary. A merged tool also can't cleanly return `status:"refused"` — refusal must happen before any save, so the translate/save boundary is load-bearing. Keeping the tools separate.

---

## Optimization pass — post-Phase-1 cleanup + cost instrumentation (2026-06-06)

Acted on the `ARCHITECTURE.md` / `OPTIMIZATION.md` analysis pass. Code-only changes;
docs reconciled here. No behavior change except the cost-ledger bug fix (#10). Grouped
by intent.

### (a) Dead-code + divergent-path removal

- **Stale comments + one doc string.** Deleted `agent.py`'s top-of-file comment naming
  nonexistent `stage1_bare_turn.py` / `stage2_loop.py` and the "handlers still stubbed"
  comment (all handlers are real). Fixed `skills/save-to-vault.md` from `model="opus-4-8"`
  to the literal `"claude-opus-4-8"` the code actually records. Doc/comment only.
- **`save_to_vault` schema: dropped `filename`.** It was required but ignored — the output
  stem always comes from `drive_filename` via `vault_output_path` (`Path(drive_filename).stem`).
  The model was spending output tokens on a discarded field. Schema/handler mismatch closed.
- **Removed the dead `_ROUTING_PROMPT` import-time read in `translation_engine.py`.** The
  engine read the routing prompt at import and never used it (the agent loads it itself).
  Removes an import-time `read_text` with no consumer.
- **Consolidated `sha256_of` to `manifest`.** `manifest.sha256_of` is now the single source.
  The engine dropped its own copy entirely (it no longer references the function at all once
  its callers were deleted — see #6). `init_translation_log.py`'s self-contained copy left
  untouched by design (run-once bootstrap, predates `manifest.py`, zero payoff to touch).
- **Deleted `list_drive.py` and `hello_world.py`** — a superseded debug lister
  (`drive.list_folder_children` covers it, paginated) and a tutorial smoke-test leftover.
  Imported by nothing.
- **Deleted the divergent manual scripts `translate_one.py` and `translate_image_pdf.py`.**
  They bypassed the sole-writer manifest lock (remove-then-append instead of
  `manifest.upsert_entry`) and saved to the course root with no type subfolder — a parallel,
  wrong path model the agent supersedes. **Cascade (confirmed landed):** they were the only
  external callers of `translation_engine.infer_type` (and its `_TYPE_KEYWORDS` table) and of
  `engine.sha256_of`, so all three went dead and were removed. `TYPE_TO_FOLDER` and
  `vault_output_path` **stay** — the agent's save path still needs them.

### (b) `fetch_signal_detail` — RETAINED (decision, not removal)

`OPTIMIZATION.md` queued `fetch_signal_detail` + the `:signals_full` offload for removal on
the 0-calls-in-Phase-1 evidence. **Decided to KEEP it.** 0 calls means the scalar signals
carried every mode decision so far — not that a future genuinely-ambiguous file (typed-over-
handwritten hybrid, garbage-shape disambiguation) won't need the per-page detail. It's cheap
to keep (wired, out of message history behind a handle) and expensive to reconstruct later.
Added a retention comment at its definition with a re-evaluation trigger. This resolves the
STATUS/PHASE2_NOTES "keep or drop" open question to **KEEP-with-note**.

### (c) Enablers for the routing pre-pass

- **`drive.py` lazy service init.** OAuth now fires on the first Drive call via a
  `get_service()` singleton (`_service=None`), not at import. `import drive` is now
  credential-free — importable in tests/CI without auth. (`import agent` still constructs
  `Anthropic()` at import — separate concern, deliberately unchanged.)
- **New module `dedup.py`.** Lifted the md5-gate + hash/cross-ID-SHA dedup *decision* logic
  out of `agent.read_file_logic` into pure verdict functions (`md5_gate`, `hash_dedup`) that
  do no I/O — no Drive, no network, no anthropic, no cache write; caller owns fetching and
  ordering. `read_file_logic` now calls them; verdict shapes copied byte-for-byte, behavior
  identical (`test_dedup.py` added). This is the single testable source of dedup truth and a
  prerequisite for the deterministic pre-pass, which must run without an API key.

Both (c) items are the named prerequisites for PHASE2_NOTES #11 — now met.

### (d) Cache-aware cost ledger + a real bug fix

New module `costs.py` (stdlib only — no anthropic/drive/client, importable bare) plus a
per-call ledger. `record_call()` runs after every `messages.create` and appends an
OTel-aligned JSON line to `logs/ledger_<run_id>.jsonl` (input / cache_creation / cache_read /
output tokens, tiered cost, category `routing` | `translation_text` | `translation_image`,
duration). Duration is measured tight around the API call (excludes pypdf/rasterise).

- **Bug fixed (real, ~10× routing overstatement).** The old `_calc_cost` billed
  `cache_read` tokens at the full input price. The four token classes are disjoint in the
  usage object, so cache reads were ~10× overcounted — and routing is the cache-heavy half,
  so the routing cost figure was materially inflated. Cost is now tiered: **$5 input /
  $6.25 cache-write / $0.50 cache-read / $25 output** per M. `_calc_cost` now delegates to
  `costs.tiered_cost` (signature/return/manifest unchanged — translation calls carry no cache
  tokens, so their cost is identical; only the cached routing call moves).
- **Engine seam.** An optional `on_usage(response, duration_ms)` callback on the translate
  functions (return shapes byte-identical). The agent records translation calls via the
  callback and the routing call inline. `LEDGER_PATH=None` skips ledger writes (import-clean
  for tests); the summary rolls up from in-memory rows.
- **End-of-run summary** now reports true total cost, the routing-vs-translation split,
  tool-call counts (logged separately — tools carry no tokens), and token totals by type.
  This closes the open half of PHASE2_NOTES #8.
- **Consequence:** "routing ≈ 88% of run cost" becomes **measurable per-run** going forward.
  It is still a single hand-computed observation today, not yet a multi-run distribution —
  the ledger is the mechanism that will produce that distribution.
