# Day 2 Checklist — Hebrew Lecture Translator

> ⚠️ **HISTORICAL — execution log for Day 2, complete.** Architecture has evolved substantially since (autonomy reversal, Opus 4.8, caching/offloading/md5-gate). For CURRENT architecture see `hebrew_translator_project_plan.md` and `HISTORY.md`.

**Goal**: Move from "translates one text PDF on demand" to "agent translates a whole course folder, including handwritten lectures, on its own."

**Estimated time**: 6–8 hours. Realistically two evenings or one Saturday.

**Covers**: Phase 1 steps 1.14b, 1.15, 1.16, 1.17, 1.18, 1.19.

**Prerequisites**: Day 1 complete. `translate_one.py` working on text PDFs. `translated_log.json` exists with bootstrap data. `git status` clean before starting.

---

## ⚠️ Security Reminder

Same rules as Day 1. Before every push:
```
git status
```
Must not see: `.env`, `credentials.json`, `token.json`, `.venv/`, `__pycache__/`, `*.log`, translated `.md` files, source PDFs.

`translated_log.json` IS committed (it's state, not a log).

---

## Part 0 — Cleanup & Calibration (1 hour)

Before adding new code, fix known issues from Day 1 and gather more prompt-issue data.

### Step 0.1: Normalize `translated_log.json` paths (Step 1.14b)

Current state: some `md_path` values are absolute (`/Users/saharsaygon/Documents/Obsidian Vault/...`), others are relative. Will break in cloud (Phase 3) and leaks username.

Ask Claude Code:

```
Read translated_log.json. For every entry where md_path is non-null, 
strip any vault-prefix and normalize to a relative path from the 
vault root.

Vault root: /Users/saharsaygon/Documents/Obsidian Vault/

Example transformations:
- "/Users/saharsaygon/Documents/Obsidian Vault/Design of Algorithms/Lectures/Lecture06_EN.md"
  → "Design of Algorithms/Lectures/Lecture06_EN.md"
- "Design of Algorithms/Lectures/Lecture07_EN.md"
  → unchanged (already relative)

Print a diff of what changed before writing. Do not modify entries 
where md_path is null.

Also: confirm every non-null md_path actually exists on disk under 
the vault root. If any are missing, list them — could be a path 
mismatch or a file moved manually.
```

✅ **Done when**: all `md_path` values are vault-relative, all referenced files exist on disk.

---

### Step 0.2: Decide on `bootstrap_log.py`

Two options:
- **Rename + document** to `init_translation_log.py` with a header comment explaining "Run this only when seeding `translated_log.json` from scratch (e.g., after major schema change). Not for regular use."
- **Delete** if it's truly one-shot and the seed data is locked in.

Pick one. Don't leave anonymous scripts in repo root.

✅ **Done when**: either renamed with header comment OR removed from repo.

---

### Step 0.3: Translate 2–3 more text PDFs

Pick varied files from courses NOT yet translated (check `translated_log.json` for `not_translated_yet` entries — there are several Linear Systems and Semiconductor files marked pending).

For each, log in `progress_log.md`:
- Cost (input + output tokens)
- Whether the prompt issues from Day 1 reappeared (preamble, fake date, intermediate output)
- Any new issues

After translating, the agent (Day 2) will need to update `translated_log.json` for these. For now, since we're using `translate_one.py`, manually update the entries to reflect the translation OR skip the manifest update and let the agent re-run later (the hash will match what was bootstrapped).

### Step 0.4: Prompt revision pass (Step 1.19)

If the issues from Day 1 reappeared in any of the 3 new translations, the rules in `translation_system_prompt_agent.txt` aren't strong enough. Tighten them. If they didn't reappear, leave the prompt alone.

Don't iterate per-file. One revision, applied to all observations at once.

✅ **Done when**: 2–3 more `.md` files in Obsidian, prompt either unchanged or revised once with reasoning logged in progress_log.

---

## Part 1 — Image PDF Support (2–3 hours)

This is what unlocks your handwritten lecture archive. Most of your existing notes go through this path.

### Step 1.1: Install poppler (system dependency for pdf2image)

`pdf2image` needs poppler binaries. On Mac:
```
brew install poppler
```

Verify:
```
pdftoppm -v
```
Should print version info.

✅ **Done when**: poppler responds.

---

### Step 1.2: Pick a handwritten test PDF

Choose a single handwritten lecture from your Drive — ideally short (3–5 pages) for fast iteration. Note its Drive file ID. A Linear Systems or Semiconductors file from `not_translated_yet` is ideal — those are likely handwritten.

---

### Step 1.3: Build `translate_image_pdf.py`

Linear, like `translate_one.py`. Don't try to combine modes yet.

Ask Claude Code:

```
Write translate_image_pdf.py — a linear script for handwritten/scanned 
PDFs. Mirror the structure of translate_one.py but use vision instead 
of text extraction.

Inputs (hardcode for now):
- DRIVE_FILE_ID = "<I'll paste>"
- COURSE_HEBREW = "<I'll paste>"

Steps:
1. Load .env, courses.json, system prompt — same as translate_one.py
2. Authenticate to Drive, download the file by ID
3. Hash source bytes (sha256), check translated_log.json — if hash 
   matches an entry with non-null md_path or skipped_permanent status, 
   print "already translated/skipped" and exit
4. Convert each PDF page to a PNG image with pdf2image (200 DPI is 
   plenty for handwritten text — don't over-resolve, it just costs more)
5. Build the Anthropic message with vision blocks: one image content 
   block per page, in order, followed by a text instruction "Translate 
   this Hebrew lecture per the system prompt."
6. Call Opus 4.7 with max_tokens=16000
7. Save the .md to the correct Obsidian course folder using path 
   relative to vault root
8. Update translated_log.json: append new entry OR update existing 
   entry (match by drive_file_id) with md_path, course, type, 
   translated_at, model="opus-4-7", and real cost/token data from 
   response.usage
9. Print path on success, print token usage at the end

Use base64 encoding for images per the Anthropic vision API spec. 
Use pathlib for all paths. Wrap the API call in try/except. Always 
read/write JSON with encoding='utf-8'.

Walk me through the vision-specific parts I haven't seen before — 
how the content array works with mixed image+text blocks, how 
base64 image data is structured.
```

**Cost flag**: image tokens are roughly 1.6K per page. A 10-page handwritten lecture = ~16K input tokens just for images, ~$0.24 input + ~$0.50 output = ~$0.75 per lecture. Roughly comparable to text-mode for now, but it scales worse with page count.

✅ **Done when**: handwritten PDF translates, output appears in Obsidian, `translated_log.json` updated correctly with cost data.

**Sanity check the output**: vision mode handles handwriting differently than typed text. Look for:
- Math notation rendered correctly (handwritten LaTeX is harder than typed)
- Diagrams described properly (this is the strong point of vision mode vs text mode)
- Hebrew handwriting variants — common letters that get misread (ב/כ, ר/ד, ה/ח)
- Whether the agent flagged uncertain readings in Translator Notes

---

### Step 1.4: Compare quality vs text mode

If you have a single lecture available as both a typed PDF and as your handwritten notes, translate both and compare. This calibrates which mode is "good enough" for which source type going forward.

✅ **Done when**: you have a gut sense of vision mode's quality bar.

---

## Part 2 — Auto-Detect Text vs Image PDF (1 hour)

Now combine the two scripts into one with automatic mode selection.

### Step 2.1: Write the detection heuristic

Ask Claude Code:

```
Write a function detect_pdf_mode(pdf_bytes) that returns either 
"text" or "image".

Logic:
1. Try text extraction with pypdf
2. If extracted text is empty or under ~50 characters total: return "image"
3. Otherwise, count what fraction of "words" are recognizable:
   - Split on whitespace
   - A word is "recognizable" if it contains at least one Hebrew letter 
     OR is a recognizable Latin/digit token (regex: matches [A-Za-z]{2,} 
     or [0-9]+ or common math symbols)
   - Reject pure-junk tokens (random Unicode, single non-Hebrew letters, 
     repeated punctuation)
4. If recognizable fraction < 50%: return "image"  
5. Otherwise: return "text"

Add unit tests with sample inputs:
- Empty string → "image"
- Clean Hebrew paragraph → "text"
- Garbled OCR-style output (random unicode, dots, single chars) → "image"
- Mixed Hebrew + LaTeX → "text"

Show me the function and tests before integrating.
```

✅ **Done when**: function passes its own tests and you've manually run it on 2–3 real PDFs (one text, one handwritten) and gotten the right answer.

---

### Step 2.2: Merge into a unified entry point

Refactor — but keep the two translation functions separate. They'll become tools in the agent loop later.

Ask Claude Code:

```
Refactor: extract translate_text_pdf() from translate_one.py and 
translate_image_pdf() from translate_image_pdf.py into a shared 
module (e.g., translation_engine.py). Keep them as separate 
functions — don't merge.

Then create translate_smart.py that:
1. Always download the PDF first
2. Hash + check translated_log.json (skip if already done)
3. Call detect_pdf_mode() on the bytes
4. Dispatch to translate_text_pdf() or translate_image_pdf()
5. Print which mode was chosen and why ("TEXT mode — 87% recognizable words")
6. Save .md and update translated_log.json the same way regardless of mode

Both translate_*.pdf functions should accept the same signature so 
the dispatch is clean.

Show me the module structure before running it.
```

✅ **Done when**: same script handles both text and handwritten PDFs by file ID alone, with hash-based dedup.

---

## Part 3 — Console Summary (Step 1.17, 30 min)

### Step 3.1: End-of-run summary

Currently each script prints one path on success. For the agent, we'll need structured summaries. Start small.

Ask Claude Code:

```
At the end of translate_smart.py, print a structured summary block:

==========
TRANSLATION SUMMARY
Source: <Drive filename>
Mode: <text | image | skipped (hash match)>
Course: <English course name>
Output: <vault-relative path to .md>
Input tokens: <n>
Output tokens: <n>
Cost estimate: $<computed from Opus 4.7 pricing>
Duration: <wall-clock seconds>
==========

Use $15/M input, $75/M output for Opus 4.7 cost calculation. Pull 
token counts from the API response (response.usage). For skipped 
files, only print Source / Mode / "Already translated at <path>" / 
Duration.
```

✅ **Done when**: every translation prints the summary block.

---

## Part 4 — The Agent Loop (Step 1.18, 3 hours)

This is the heart of Day 2. You're wrapping the now-working translation function in a tool-use loop with Opus 4.7 making routing decisions.

### Step 4.1: Design the tool set on paper first

Before writing code, sketch each tool's signature on paper or in a scratch file. The tool design IS the architecture.

The six tools and their roles:

**`list_folder(folder_id)`** → returns list of `{name, id, type: "file"|"folder", mime_type}` for children of the given Drive folder. The agent's eyes.

**`read_file(file_id)`** → downloads, hashes, checks `translated_log.json`. If already-translated, returns `{status: "already_done", md_path: ...}`. Otherwise extracts content, returns `{status: "ready", type: "text"|"image", content: ...}`. The auto-detection from Step 2.1 lives inside this tool.

**`translate_file(file_content, course, source_type, drive_file_id, drive_filename, source_hash)`** → sends content to Opus 4.7 with the translation system prompt. Returns translated markdown. (Note: this is a *separate* Opus call from the agent's main loop. The agent decides what to translate; the translation engine actually does it.) Captures cost/token data for log update.

**`save_to_vault(course, filename, content, file_type, drive_file_id, drive_filename, source_hash, cost_data)`** → writes the .md to the correct Obsidian folder. Updates `translated_log.json` with the new entry (model="opus-4-7", real costs). Returns the vault-relative path.

**`ask_user(question, context)`** → prints the question to the terminal, blocks for a typed response. Used when the agent is genuinely uncertain. Returns the user's answer as a string.

**`update_mapping(hebrew_name, english_name, mapping_file)`** → after `ask_user` confirms a new course translation, persists it to `courses.json`. Never called without prior `ask_user` approval.

Sketch each one's inputs, outputs, and error modes before writing code. Pay attention to which tools touch `translated_log.json` (read_file reads it, save_to_vault writes it).

✅ **Done when**: you have a one-page tool spec that you understand fully.

---

### Step 4.2: Build the agent loop skeleton

Ask Claude Code:

```
Build agent.py — the main agent loop. Don't implement tools yet, just 
the loop structure.

Inputs:
- ROOT_FOLDER_ID = "<the iPad notebook folder ID>"  
  (will become a CLI arg in Phase 2)

Structure:
1. Load .env, courses.json, translated_log.json, system prompt 
   (which now contains the Agent Routing & Traversal section we 
   wrote on Day 1)
2. Define tool schemas using Anthropic's tool-use format. For each of 
   the six tools, write the JSON schema (name, description, 
   input_schema). Don't implement them — just stub with 
   raise NotImplementedError.
3. Initialize message history with a single user message: 
   "Start translation run on folder ID <ROOT_FOLDER_ID>. Apply the 
   routing policy from the system prompt. Use translated_log.json 
   for dedup."
4. Run the tool-use loop:
   - Call client.messages.create() with system prompt, messages, tools
   - If response has stop_reason "tool_use": extract the tool_use blocks, 
     dispatch to handler functions (which raise NotImplementedError 
     for now), append tool_result blocks to messages, loop.
   - If stop_reason is "end_turn": print final response, exit.
5. Hard cap: TOOL_CALL_BUDGET = 200. Track count across the loop. 
   If exceeded, print warning and break.
6. Log every tool call with: tool name, inputs, outputs (truncated), 
   reasoning (if visible in the model's text). Write to logs/agent_<timestamp>.log.

Walk me through the tool-use loop pattern carefully — how 
stop_reason works, what tool_use vs tool_result blocks look like, 
how to keep message history correct across iterations.

I want the skeleton runnable (it'll fail at the first NotImplementedError) 
before we implement any tool.
```

✅ **Done when**: `python agent.py` runs, makes one Opus call, the model picks a tool, and the script crashes cleanly with NotImplementedError. Loop structure is correct.

---

### Step 4.3: Implement tools one at a time

Order matters. Each tool depends on the previous ones working.

#### 4.3a: `list_folder`
Wraps the Drive API call you already wrote in `list_drive.py`. Returns structured JSON, not just names.

Test: agent now lists the root folder, then probably crashes trying to call `read_file` next. Good — that's the loop working.

#### 4.3b: `read_file`
Wraps download + hash + manifest check + auto-detection from Step 2.1. Returns content with type, OR returns `already_done` if the hash matches an entry in `translated_log.json`.

This tool is doing the hash-based dedup work — by the time the agent considers translating a file, this tool has already filtered out duplicates.

Test: agent reads one file. If that file is already in `translated_log.json` as translated, agent should get `already_done` and move on without trying to translate. Verify on a Lecture 1–7 file (already in manifest).

#### 4.3c: `translate_file`
This is a separate Opus 4.7 call from inside the tool. The agent's main loop is doing routing; this nested call does the actual translation.

Pattern: tool receives content + metadata, makes its own `client.messages.create()` call with the translation system prompt, returns the markdown plus the usage data so `save_to_vault` can log costs accurately.

Test: agent translates one file, returns to the main loop, decides what to do next.

#### 4.3d: `save_to_vault`
Writes to disk + updates `translated_log.json` atomically. Both must succeed or neither — if disk write fails, don't write to manifest.

Pattern:
1. Write `.md` to `<vault>/<course>/<type>/<filename>_EN.md`
2. Append/update entry in `translated_log.json` with vault-relative `md_path`, `model="opus-4-7"`, real cost data
3. Return the vault-relative path

Test: full single-file flow now works through the agent loop. Manifest entry has real cost numbers (not zero).

#### 4.3e: `ask_user`
Synchronous terminal prompt. Use Python's built-in `input()`. Print the question with clear context — the agent should pass enough info that you can answer without context-switching.

Pattern in the prompt:
```
NEW COURSE FOLDER FOUND
Hebrew name: פונקציות מרוכבות
Path: /iPad notebook/פונקציות מרוכבות/
Proposed English: "Complex Functions"
[A] Approve, [R] Reject and provide alternative, [S] Skip this folder
> 
```

Test: trigger by running on a folder with an unmapped course. Verify agent waits, accepts your input, proceeds correctly.

#### 4.3f: `update_mapping`
Writes to `courses.json`. Should only be called *after* `ask_user` returned an approval. Add a defensive check: don't update without a prior `ask_user` call in the message history. (Phase 2 can polish this; just check it crudely for now.)

Test: approved course mapping persists to disk. Re-run agent on same folder — it doesn't ask again.

✅ **Done when**: all six tools implemented, agent runs end-to-end on a small test folder.

---

### Step 4.4: First real agent run

Pick a course folder where most files are already in `translated_log.json` — Design of Algorithms is a good candidate (Lectures 1–7 done, plus tutorials and homework). The agent should:
- Skip everything already in the manifest (via `read_file` returning `already_done`)
- Translate only Lecture 8 (currently `not_translated_yet`) and any new files

This is the perfect first-real-run test because most of the work is verifying the dedup, not actually translating.

Ask Claude Code:

```
Run agent.py against the Design of Algorithms course folder (folder ID: <X>). 
Watch the tool calls in the log.

Things to verify:
- Files already in translated_log.json with non-null md_path → 
  read_file returns "already_done", agent skips translation
- Files marked skipped_permanent → also skipped
- not_translated_yet files → translate normally
- Routing decisions match what I'd choose myself (lectures → Lectures/, 
  homework → Homework/, פתור folders skipped)
- ask_user fires only on genuinely ambiguous cases, not common ones
- Tool-call count stays well below 200
- translated_log.json gets updated with real cost data for new translations

Expected: most files skipped via dedup, 1-2 actually translated.
Expected total cost: <$2 (most work is just listing folders and 
checking the manifest, very cheap).

If anything goes wrong, paste the log and we'll debug.
```

✅ **Done when**: course folder fully processed, only the genuinely-new files translated, manifest updated correctly.

---

## Part 5 — Commit & Wrap (15 min)

### Step 5.1: Commit Day 2 work

Run `git status`. Expected new/changed files:
- `translate_image_pdf.py` (or merged into `translation_engine.py`)
- `translate_smart.py` (or whatever the unified entry point is named)
- `translation_engine.py` (if extracted)
- `agent.py`
- `translated_log.json` (modified — new entries, normalized paths)
- `courses.json` (possibly modified — new approved mappings)
- `bootstrap_log.py` removed OR renamed to `init_translation_log.py`

Should NOT appear:
- The translated `.md` files (they live in Obsidian)
- Test PDFs you may have downloaded locally
- `.env`, `credentials.json`, `token.json`
- `logs/` folder (verify `*.log` and/or `logs/` are in `.gitignore`)

```
git add .
git commit -m "Day 2: image PDF support, auto-detection, agent loop, manifest integration"
git push
```

### Step 5.2: Update progress_log.md

Log:
- What worked, what surprised you
- Real cost data from the small-course agent run
- Hash-dedup performance — how many files correctly skipped
- Any new prompt issues (the agent's routing reasoning may reveal weaknesses in the system prompt that single-file translation didn't)
- Open questions for Day 3

---

## Day 2 Success Criteria

You're done when all are true:

- [ ] `translated_log.json` paths normalized to vault-relative, no absolute paths remain
- [ ] `bootstrap_log.py` either renamed with header or deleted
- [ ] Image PDFs translate successfully via vision mode
- [ ] Auto-detection picks the right mode for any PDF without manual flagging
- [ ] Hash-based dedup works: re-running on the same file is a no-op
- [ ] All six agent tools implemented
- [ ] Agent loop runs end-to-end on a small real course folder
- [ ] Routing decisions match your judgment on test cases
- [ ] `ask_user` fires when expected, doesn't fire when not
- [ ] Tool-call cap is respected
- [ ] `translated_log.json` gets real cost data populated for new translations (not zeros like the manual entries)
- [ ] Per-run log file is generated
- [ ] No secrets in Git after final push

---

## Common Snags

**`pdf2image` fails with "Unable to get page count"** → poppler not installed or not on PATH. `brew install poppler` (Mac) and verify `pdftoppm -v` works.

**Image API call exceeds token limit** → too many pages or DPI too high. 200 DPI is usually fine for handwritten Hebrew. If a single lecture is over 30 pages, consider splitting it into chunks (Phase 2 problem).

**Agent loops forever calling `list_folder` repeatedly** → routing policy in system prompt isn't directing it to terminate. Check whether the agent is being told to keep going after a folder is fully processed. The system prompt should include a "when done with the root folder, end the run" instruction.

**Agent calls `ask_user` constantly on obvious cases** → routing policy is too cautious. Tighten the system prompt's "when uncertain" criteria; add examples of what doesn't count as uncertain.

**Agent never calls `ask_user`, just guesses** → opposite problem; routing policy is too aggressive. Add explicit examples of when ask_user is required.

**Tool returns wrong type, agent crashes** → tool result must be a string in the API. JSON-encode structured outputs before returning. Don't pass Python dicts directly.

**Unicode errors writing `.md` files** → always open with `encoding='utf-8'` explicitly. Same as Day 1.

**Cost spikes unexpectedly** → check the log for repeated translation of the same file (indicates the dedup check in `read_file` isn't running or is broken). Or the agent re-listing the same folder many times. Tool-call cap should have caught it; if not, lower the cap.

**`translated_log.json` corruption** (e.g., agent crashed mid-write) → restore from Git. This is why it's committed. `git checkout HEAD -- translated_log.json` then re-run. Worth wrapping the writes in atomic-write patterns (write to temp file, then rename) in Phase 2.

**Hash matches but file isn't actually translated** → manifest entry says `model="manual"` but the `md_path` points to a file that doesn't exist on disk. This is what Step 0.1's existence check catches. If found, set `md_path: null` and `model: "not_translated_yet"`.

---

## What's Next — Day 3

After Day 2, Phase 1 is essentially done. Day 3 is polish + Phase 2 entry:

- Step 1.20: full refactor pass with Claude Code (clean structure before Phase 2)
- Step 1.21: first run against the entire iPad notebook folder
- Phase 2 Step 2.1: CLI args (`--folder`, `--dry-run`, `--force`) — start using argparse properly
- Phase 2 Step 2.6: error handling so one bad file doesn't crash a 60-file run; new manifest states (`skipped_transient`, `error_message`)

---

## Final Reminders

- ⚠️ Before every push: `git status`
- Implement tools in dependency order, test after each
- Don't combine text + image modes into one function — keep them separate, the agent will dispatch
- The system prompt is the routing logic; if the agent is doing the wrong thing, fix the prompt before fixing code
- Hash-based dedup is the foundation — if it's broken, the agent will retranslate everything and burn money
- Stuck >30 min → ask Claude Code
- Cost ceiling sanity: a course folder where most files are already in manifest should be <$2 (the agent is mostly listing + checking, not translating)
