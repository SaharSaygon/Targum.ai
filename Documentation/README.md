# Documentation index

Read in this order when picking the project back up:

| File | What it is | Freshness |
|---|---|---|
| `STATUS.md` | Current state + next step. **Start here.** | Living — updated every session |
| `unattended_runs.md` | How the launchd automation works + one-time setup, testing, cadence changes. | Living — operational doc |
| `FLOWCHARTS.md` | Mermaid diagrams of how a run works. Diagram 0 = the default SDK/subscription path. | Living |
| `HISTORY.md` | Append-only log of every decision, run, and fix, with dates. The "why" behind everything. | Living — append-only |
| `ARCHITECTURE.md` | Deep state-map of the run mechanics (pre-pass, tools, dedup, cache, manifest schema). Written pre-reorg — read via the scope note at the top (old→new module mapping). | Frozen 2026-06-07 + 2026-07-30 scope note |
| `PHASE2_NOTES.md` | Numbered improvement backlog from June; STATUS still cites items by number (#3b, #6, #11). | Frozen — kept for the numbering |

`archive/` holds completed plans and early planning docs (subscription/cron
plan, the Task 2 implementation prompt, the original project plan, day
checklists, the June optimization analysis). Nothing in there describes the
current system — they are kept as decision records only; `HISTORY.md`
supersedes them for "what actually happened".
