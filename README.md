# Targum.ai

Agentic Hebrew→English translator for university course material. It scans a
Google Drive course tree, decides per file what to do (translate as
lecture/tutorial/homework/exam, skip handwritten solutions, route reference
docs), translates PDFs to Markdown via the Claude API, and saves the results
into an Obsidian vault. A content-hash manifest makes runs incremental — only
new or changed Drive files are processed.

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
| `Documentation/` | Architecture, status, history, flowcharts, project plan |

Secrets (`credentials.json`, `token.json`, `.env`) are gitignored and stay in
the project root. If a run fails with `invalid_grant: Token has been expired
or revoked`, delete `token.json` and run
`.venv/bin/python -c "import drive; drive.get_credentials()"` to re-auth in
the browser.

See `Documentation/ARCHITECTURE.md` for the full design.
