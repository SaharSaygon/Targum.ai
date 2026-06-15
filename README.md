# Targum.ai

Agentic Hebrew→English translator for university course material. It scans a
Google Drive course tree, decides per file what to do (translate as
lecture/tutorial/homework/exam, skip handwritten solutions, route reference
docs), translates PDFs to Markdown via the Claude API, and saves the results
into an Obsidian vault. A content-hash manifest makes runs incremental — only
new or changed Drive files are processed.

## Cost: ~$9 → ~$0.30 per file

The agent runs on a paid API, so every run costs money. I cut the per-file
cost about 30× in three steps:

1. **~$9 — the naive version.** Every run, the agent re-read the entire
   Google Drive tree from scratch to decide what to do. It was re-paying to
   re-think files it had already handled. (I caught this after one stretch of
   re-runs cost ~$60.)

2. **~$1.20 — caching and offloading.** As the agent works through a run, its
   conversation keeps growing — the rules it follows, the list of tools it has,
   and every folder it has visited. At each step the whole
   conversation gets sent to the API again, so a longer conversation means a
   bigger bill on every single step. **Caching** uses Anthropic's *prompt
   caching*: on each call to the agent loop, the stable front of the
   conversation (the system prompt, tool definitions, and the history built up
   so far) is marked so the API reuses it instead of charging full price for it
   again — the single biggest drop. **Offloading** is the companion trick: when
   inspecting a file produces a lot of bulky detail, I keep that detail off to
   the side and let the agent fetch it only if it actually needs it, instead of
   dragging it through the rest of the conversation. Together these stop the
   bill from ballooning as the run goes on.

3. **~$0.30 — a cheap pre-check.** Before involving the (expensive) AI, a bit
   of plain code compares the Drive files against a record of what's already
   done, and hands the agent *only* the new or changed files. The AI never
   looks at the unchanged ones, so there's almost nothing left to pay for.

Translating one file always cost about $0.30 — that part never changed. All
the savings came from cutting the wasted work *around* it: the agent
re-reading folders and re-deciding things it had already settled. By the end,
the per-file cost is essentially just the translation itself, with the
overhead stripped away.

## Run

```bash
.venv/bin/python agent.py
```

Each run writes an audit log and a cost ledger to `logs/` and prints a
RUN SUMMARY (files translated, skips, refusals, total cost).

## Layout

| Path | What it is |
|---|---|
| `agent.py` | Agent loop, tool schemas, tool handlers, run summary |
| `agent_routing_prompt.md` | System prompt: routing/classification rules (read at startup) |
| `translation_engine.py`, `pdf_mode_detector.py` | Translation calls + text-vs-image signal detector |
| `drive.py`, `prepass.py`, `dedup.py`, `manifest.py` | Drive access, incremental worklist, hashing, manifest I/O |
| `courses.py` / `courses.json` | Hebrew→English course-name mappings |
| `costs.py` | Token/cost accounting |
| `skills/` | Per-tool prompt fragments loaded by the translation engine |
| `scripts/` | One-off utilities (`init_translation_log.py` — interactive manifest bootstrap) |
| `translated_log.json` | The manifest: every known Drive file with hash, vault path, or skip reason |
| `logs/` | Run logs + cost ledgers (current; older runs in `logs/archive/`) — gitignored |
| `Documentation/` | Architecture, status, history, flowcharts, optimization notes |

Secrets (`credentials.json`, `token.json`, `.env`) are gitignored and stay in
the project root. If a run fails with `invalid_grant: Token has been expired
or revoked`, delete `token.json` and run
`.venv/bin/python -c "import drive; drive.get_credentials()"` to re-auth in
the browser.

See `Documentation/ARCHITECTURE.md` for the full design.
