# Day 1 Checklist — Hebrew Lecture Translator

> ⚠️ **HISTORICAL — execution log for Day 1, complete.** Architecture has evolved substantially since (autonomy reversal, Opus 4.8, caching/offloading/md5-gate). For CURRENT architecture see `hebrew_translator_project_plan.md` and `HISTORY.md`.

**Status**: ✅ **Complete** — all 20 steps executed, first real lecture translated successfully on Mac.

**Original goal**: Go from empty computer to translating your first real lecture successfully.

**Estimated time**: 4-6 hours.
**Actual time**: ~5–6 hours (including Windows PATH troubleshooting + Mac transition).

**Covered**: Phase 1 steps 1.1 through 1.14 (env setup + Drive auth + first end-to-end translation).

---

## ⚠️ Security Reminder

Files that must NEVER be in the repo:
- `.env` — Anthropic API key
- `credentials.json` — Google OAuth credentials
- `token.json` — Google access token
- `__pycache__/`, `.venv/`, `*.log`, `.DS_Store`

**Run `git status` before every push.** If a secret leaks: rotate the key in the provider's console immediately. Don't try to scrub Git history — rotation is faster and safer.

---

## Part 1 — Install Core Tools (✅ done)

### Step 1: Install Python 3.12+
- Verify: `python --version` (Mac) or `python --version` (Windows) prints 3.12.x or higher
- ✅ Done on both Windows (initial) and Mac (current)

### Step 2: Install VS Code
- Extensions: **Python** (Microsoft), **Pylance**, **GitLens** (optional)
- ✅ Installed on Mac with Python extension

### Step 3: Install Git
- Configure: `git config --global user.name` + `user.email`
- ✅ Done

### Step 4: Install Claude Code
- ✅ v2.1.114 installed on Windows (required PATH override in VS Code settings)
- ✅ Reinstalled on Mac via Homebrew, no PATH issues

---

## Part 2 — Project & GitHub Repo (✅ done)

### Step 5–6: GitHub repo
- ✅ Private repo `Targum.ai` created
- ✅ Repo cloned to Mac at `/Users/sahar/Projects/Targum_ai`

### Step 7–8: Local project + Claude Code
- ✅ Project folder open in VS Code
- ✅ `.gitignore` excludes all secrets and build artifacts
- ✅ `.venv` created and activated
- ✅ First commit pushed to GitHub
- ✅ `requirements.txt` with: anthropic, python-dotenv, google-api-python-client, google-auth-httplib2, google-auth-oauthlib, pypdf, pdf2image, Pillow

---

## Part 3 — Anthropic API (✅ done)

### Step 9–11
- ✅ API key created (Mac-specific; Windows key revoked during transition)
- ✅ Credits added to account
- ✅ `.env` contains `ANTHROPIC_API_KEY=sk-ant-...` (verified NOT in git status)
- ✅ `hello_world.py` runs, prints translation of "שלום עולם"

**Milestone**: First programmatic Claude API call working.

---

## Part 4 — Google Drive API (✅ done)

### Step 12–15
- ✅ Google Cloud project `hebrew-translator` created, Drive API enabled
- ✅ OAuth consent screen configured (External, self added as test user)
- ✅ OAuth credentials (Desktop app) downloaded as `credentials.json`
- ✅ `list_drive.py` written — authenticates, lists iPad notebook folder
- ✅ `token.json` generated on first OAuth flow (correctly gitignored)

**Milestone**: Read access to Drive established.

---

## Part 5 — First Real Translation (✅ done)

### Step 16: Obsidian vault path
- ✅ `OBSIDIAN_VAULT_PATH=/Users/sahar/Obsidian/Studies` in `.env`
- ✅ Course folders created manually inside vault

### Step 17: Mapping files
- ✅ `courses.json` created with initial Hebrew → English course name mappings
- ❌ `subfolder_types.json` — **dropped during this session**. Semantic routing replaces lookup table. See progress_log for rationale.

### Step 18: System prompt
- ✅ `translation_system_prompt_agent.txt` saved
- ✅ Adapted from "Translating Hebrew Lectures" Claude Project for agent use:
  - Added "Agent Routing & Traversal" section (semantic role classification, skip/inherit rules)
  - Added "Source and Workflow" rewritten for Drive operation, not chat upload
  - Added: response begins directly with YAML frontmatter, no preamble
  - Added: `date_translated` = today's actual date, omit if unknown
  - Added: filename wins over document header on metadata conflicts
  - Added: no intermediate output / "let me redo this" passes
  - Added to "Do Not": don't write to mapping files without ask_user approval

### Step 19: First end-to-end translation
- ✅ `translate_one.py` written — linear proof-of-concept
  - Downloads file from Drive by ID
  - Extracts text with pypdf
  - Loads system prompt from .txt file
  - Calls Opus 4.7 with `max_tokens=16000`
  - Saves `.md` to correct Obsidian course folder
- ✅ Successfully translated Lecture 9, Design of Algorithms (Hebrew → English)
- ✅ Output landed correctly in `Studies/Design of Algorithms/`
- Cost: ~$0.67 (8.5K input + 7.1K output tokens)

**Milestone**: full pipeline end-to-end working.

---

## Part 6 — Commit & Wrap Up (⏳ pending)

### Step 20: Commit Day 1 work

Run before pushing:
```
git status
```

Expected new files:
- `list_drive.py`
- `translate_one.py`
- `courses.json`
- `translation_system_prompt_agent.txt`

Should NOT appear:
- `.env`, `credentials.json`, `token.json`
- `.venv/`, `__pycache__/`
- The translated `.md` (lives in Obsidian, not the repo)
- The source PDF

Commit:
```
git add .
git commit -m "Day 1: env setup, Drive auth, first end-to-end translation"
git push
```

---

## Day 1 Success Criteria — All Met ✅

- ✅ Python, VS Code, Git, Claude Code installed and working (Mac)
- ✅ `Targum.ai` repo exists, has commits
- ✅ `.env` contains Anthropic API key, NOT in Git
- ✅ `credentials.json` and `token.json` exist, NOT in Git
- ✅ Successful API call to Claude
- ✅ Successful listing of files from iPad notebook Drive folder
- ✅ At least one Hebrew lecture translated and saved to Obsidian vault
- ✅ Translated file opens in Obsidian, reads correctly

---

## Issues Identified (To Fix in Batch After 2–3 More Translations)

Don't iterate on the prompt per-file. Translate 2–3 more, then revise once.

1. **Conversational preamble** leaked into output — already added "begin directly with YAML" rule to prompt; verify it sticks
2. **Fabricated `date_translated`** (used a date from training data, not today) — already added "today's actual date, omit if unknown" rule
3. **Filename wins over header** — already added rule, verify on a file with mismatched lecture number
4. **Intermediate / "let me redo" output** (showed multiple table versions before final) — already added "final only" rule
5. **Graph diagram figures lose structure** when working from text-extracted PDFs — Phase 2 fix (hybrid mode: vision for figure-heavy pages)

---

## Common Snags Encountered

- **Windows PATH** for Claude Code — VS Code didn't see User PATH; fixed via `terminal.integrated.env.windows` override in settings.json. Mac had no equivalent issue.
- **`max_tokens=8000` was insufficient** for first lecture (used 7.1K) — bumped to 16000 for headroom on dense lectures.
- **OAuth "unverified app" warning** on first auth — expected, click Advanced → Go to (unsafe). Safe because you made the app.

---

## What's Next — Day 2

Two parallel tracks; recommended order is A then B.

### Track A: widen file coverage (Phase 1.15–1.16, ~3 hours)
- Image PDF support via pdf2image + Claude vision
- Auto-detect text vs image PDF (OCR artifact heuristic)
- **Why first**: most of your archive is handwritten; current `translate_one.py` can't touch them. Easier to debug a linear script than an agent loop.

### Track B: build the agent loop (Phase 1.18, ~3 hours)
- Define and implement tools: `list_folder`, `read_file`, `translate_file`, `save_to_vault`, `ask_user`, `update_mapping`
- Tool-use loop with Opus 4.7
- Tool-call budget cap (~200)
- Routing/skip decision logging
- **Why second**: by this point, image-mode + text-mode are both known-working linear functions; the agent just wraps them in tools.

### Before next session
- Run `translate_one.py` on 2–3 more text PDFs to gather more prompt-issue data
- Then do a single prompt revision pass with all observations

---

## Final Reminders

- ⚠️ **Before every push**: `git status` → verify no secrets
- If stuck >30 min on one step: ask Claude Code instead of grinding
- Commit often — small commits are easier to debug than giant ones
- You shipped Day 1 in budget. The hardest setup work is behind you.
