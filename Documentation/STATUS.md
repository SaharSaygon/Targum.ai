# STATUS

> **Project name (2026-06-14):** brand + GitHub repo = **`Targum.ai`** (with the dot);
> local clone directory / filesystem = **`Targum_ai`** (dotless — illegal as a Python
> identifier with the dot). No code/module names changed by the rename. See HISTORY
> "Project rename: → Targum.ai / Targum_ai (2026-06-14)".

*Last updated: 2026-07-30.*

## Current state

**Phase 1 COMPLETE; the agent is in routine incremental operation.** Since mid-June it
runs every few days: the deterministic pre-pass diffs the Drive tree (md5 metadata only,
no byte reads) and hands the loop only new/changed files; the loop translates, skips, and
records autonomously. **8 tools** unchanged: `list_folder`, `read_file`,
`translate_text_pdf`, `translate_image_pdf`, `save_to_vault`, `update_mapping`,
`fetch_signal_detail`, `skip_file`.

The Drive tree has grown **213 → 461 files** (a solved-exam corpus for Introduction to
Semiconductor Devices landed for exam prep, plus first non-university content —
"Lior's Course"). The manifest holds **482 entries**: 379 `claude-opus-4-8`, 35 `manual`,
15 `claude-opus-4-7`, **53 `skipped_permanent`** (all md5-backed — the pre-pass drops
every skip for free).

**Config externalized (2026-06-15, `2b416a7`).** Per-user settings (`root_folder_id`,
`vault_path`, `model`, `spend_cap_usd`, `tool_call_budget`) live in a gitignored
`config.json` read through `config.py` (frozen dataclass, validated, 11 tests). Secrets
stay in `.env`. Note: `spend_cap_usd` is **enforced on the api path since
2026-07-30** (the loop stops cleanly at the cap; subscription path ignores it —
no per-token dollars). `run_interval_days` (added 2026-07-30) sets the
unattended cadence.

**Drive/engine fixes (2026-07-16, `fbf419c`).** Google-native Docs/Sheets/Slides export
via `export_media`; revoked-refresh-token (`invalid_grant`) fallback to interactive OAuth;
image pages pre-downscaled to 1568px (API 2000px multi-image limit); image-mode output cap
**16K → 64K tokens with streaming** — fixed silent mid-derivation truncation on long
lectures.

**Routing policy (current).** Two deliberate skip cases, both the user's OWN solutions —
(a) handwritten עבוד-homework + פתר-solution files (signal-decided), (b) the user's own
exam solutions (ownership-decided: `פתרונות שלי` / `שלי` / "my" marker, OR handwritten
signals) — **plus the לתרגם TRANSLATE OVERRIDE (added 2026-07-20)**: a path segment
containing the stem לתרגם is an explicit user instruction to translate everything under
it, disabling both skip cases (never `skip_file` under it; normal type/mode rules apply).
Added after the ownership rule correctly-but-unwantedly skipped 11 solved exams the user
needed for exam prep; renaming the folder `פתור-לתרגם` + two re-runs translated all 11.

**Validated live since June:** the homework-solution skip rule (13+ correct skips across
routine runs), the exam-ownership rule (first `פתרונות שלי` firing, 2026-07-16), the
refusal→image-retry path (garbled extraction refused in text mode, translated on image
retry, 2026-07-20), the `custom_subfolder` escape hatch (formula sheet → `Reference/`),
the md5 change-detection gate (re-translated an edited source same-day, 2026-07-22), and
`fetch_signal_detail` (first-ever real calls, 2026-07-16 — vindicating the KEEP decision).

## Recent runs (2026-07-16 → 2026-07-22)

| Run | Worklist | Translated | Skips | Total cost | Notes |
|---|---|---|---|---|---|
| `agent_20260716_082722` | 26 | 16 | 8 | $7.24 | 4-week catch-up; first ownership skip |
| `agent_20260720_151507` | 50 | 35 | 12 | $8.69 | exam-corpus influx; 1 refusal → image retry |
| `agent_20260720_203405` | 14 | 9 | 0 | $3.84 | post-לתרגם override re-run |
| `agent_20260720_212411` | 5 | 2 | 0 | $1.40 | remaining solved exams |
| `agent_20260722_143216` | 5 | 2 | 0 | $2.20 | עזרתון tutorials |
| `agent_20260722_145826` | 12 | 9 | 0 | $3.60 | reconstructed exams |
| `agent_20260724_173440` | 26 | 18 | 5 | $14.16 | עזרתון + exam-reconstruction influx; 3 hash-dedup `already_done`; 4 ownership + 1 handwritten skip; לתרגם override correctly split duplicate-content copies |

Routine incremental runs hold at ~$0.2–0.5 routing; the big catch-up/influx runs are
the outliers. 0 errors across all runs; the one refusal was handled correctly.

## Subscription port LANDED (2026-07-29)

The plan of `Documentation/archive/subscription_and_cron_plan.md` (as revised
2026-07-29) is implemented and live:

- **Repo reorg**: `core/` (shared modules incl. new `paths.py`, `vault.py`,
  `pdf_images.py`) + `legacy/` (API pay-per-token path) + `sdk/` (subscription
  path). State files stay at root; a missing manifest is now a HARD error
  (silent-[] would have re-translated the tree). Entry points:
  `python -m sdk.agent_sdk` (default) / `python -m legacy.agent` (fallback).
- **Default model `claude-opus-5`** on both paths (legacy text mode now streams
  at 32K for opus-5's default thinking; API-classifier refusals map onto the
  REFUSED: contract).
- **Shape D, two sessions per file**: ROUTE session (system =
  `agent_routing_prompt.md` only — ALL routing judgment stays with the model,
  structured decision out) then, when translating, a just-in-time TRANSLATE
  session (system = the skills, exactly the legacy engine composition; the .md
  is Written straight to the vault — the 64K-truncation bug class is gone).
  Python owns loop/retries/recording; `concurrency: 3` files in flight;
  `auth_mode: "subscription" | "api"` in config.

**Validated 2026-07-29:** 10/10 new unit tests; 4/4 live routing-regression
cases (ownership skip, לתרגם override, שלי marker, image-mode rubric) with
cross-session prompt-cache hits; first live run `agent_sdk_20260729_154820` —
10-file worklist → 1 translated + 4 ownership skips + 3 dedup, 0 errors, 255s.
Found live and fixed: googleapiclient service is not thread-safe under the
concurrent fan-out → per-thread services in `core/drive.py`.

## Next step

**Task 2 — launchd automation: LIVE since 2026-07-30** (see
`unattended_runs.md`, HISTORY same date). `ai.targum.agent` is bootstrapped:
daily 18:00 trigger, wrapper due-check at `run_interval_days: 3`, macOS
notifications, commit-only. Keychain token stored; wrapper verified same day
(real due run OK + two silent no-ops). **Supervised rollout in progress**:
watch the first 2–3 scheduled firings (next due 2026-08-02 18:00 —
notification + `logs/launchd.log` + auto-commit), then this line can drop to
routine operation.

## Deferred (not blocking)
- Disk-persisted cache as the HARD save-after-translate guarantee (the soft
  save-immediately rule is the current mitigation — see PHASE2_NOTES #3b).
- Standing manifest-integrity `--audit` mode (the md5 backfill was a one-off
  manual run of this logic — PHASE2_NOTES #6).
