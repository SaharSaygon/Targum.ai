# Implementation prompt — Task 2: unattended launchd automation

*Hand this file to a Claude Code session in this repo ("implement
Documentation/task2_launchd_prompt.md") when ready to build the automation.
All architectural decisions below were made 2026-07-27 → 2026-07-29 and are
settled — do not re-litigate them; ask only about the "Owner inputs" at the
bottom before wiring the schedule.*

---

## Context (read first)

- `Documentation/subscription_and_cron_plan.md` — Task 2 section (the original
  plan; this prompt supersedes details where they conflict).
- `Documentation/STATUS.md` — current state: the subscription port (Shape D)
  is live; `python -m sdk.agent_sdk` is the default entry point, billed to the
  owner's Claude Max plan; `python -m legacy.agent` is the API fallback.
- `Documentation/FLOWCHARTS.md` diagram 0 — how a run works.

Goal: the agent runs by itself every N days on this Mac, safely, visibly, and
recoverably — no laptop-independence requirement (launchd was chosen over
GitHub Actions / cloud schedules precisely because the vault, `token.json`
with its *interactive* OAuth fallback, and the repo are all local).

## Settled decisions

1. **Scheduler: launchd** (user LaunchAgent). Fires **daily** at a fixed hour;
   the wrapper script decides whether a run is actually due.
2. **Cadence is config, not plist**: `run_interval_days: <int>` in
   `config.json` (add to `core/config.py` as an optional key, default 7,
   validate ≥ 1). The wrapper compares against a `logs/last_run` stamp file
   and exits silently when not yet due — changing cadence is a one-number
   config edit, no `launchctl` reload.
3. **Auth: subscription by default** (`auth_mode` already in config). The
   wrapper sources `CLAUDE_CODE_OAUTH_TOKEN` from the macOS Keychain via
   `security find-generic-password` (launchd sessions don't inherit the
   user's shell login state reliably); `.env` is only needed for
   `auth_mode: "api"`.
4. **After each run**: commit `translated_log.json` (+ `courses.json` when
   changed) locally with a `state: unattended run <run_id>` message. Push is
   an owner input (below).
5. **Every unattended run must be visible**: a run-summary notification at the
   end (channel = owner input). Content comes from the run summary the agent
   already prints/logs: translated / skips / already_done / transient errors /
   API-equivalent cost / wall-clock.

## Build order

### 1. Prerequisites (before any scheduling)

a. **`spend_cap_usd` enforcement — API path only.** In `legacy/agent.py`'s
   loop, stop cleanly when the run's accumulated cost (routing + translation,
   already tracked in `LEDGER_ROWS`/`total_cost`) would exceed
   `CONFIG.spend_cap_usd`; log and report it in the RUN SUMMARY. The SDK path
   ignores the cap (subscription — dollars don't apply); document that in
   `config.example.json`'s note.

b. **Failure visibility (adapted from the original "skipped_transient" item).**
   The SDK path already leaves transiently-failed files UNRECORDED so the next
   pre-pass retries them — the retry semantics the original plan wanted are
   built in. What's missing is visibility: make sure the summary's
   `transient errors` list (file + reason) reaches the notification, and add
   a non-zero process exit code when a run ends with transient errors or
   unresolved events, so launchd logs distinguish clean runs from degraded
   ones. A persisted `skipped_transient` manifest state is NOT needed — do not
   add one.

c. **Run-summary notification.** Implement in the wrapper (not in Python) so
   it also fires when the agent crashes before printing a summary: parse the
   tail of `logs/agent_sdk_<run>.log` (or report "run crashed, see log").
   Channel per owner input; for macOS use `osascript -e 'display
   notification ...'`; for email, the plan suggested Resend.

### 2. Wrapper — `scripts/run_agent.sh`

Order: due-check (`run_interval_days` vs `logs/last_run`, exit 0 quietly if
not due) → `cd` to the repo root → export `CLAUDE_CODE_OAUTH_TOKEN` from
Keychain → run `.venv/bin/python -m sdk.agent_sdk` (respect `auth_mode`;
`--auth-mode api` only if config says so) → write the stamp file only on a
run that actually started → append exit status to `logs/launchd.log` → fire
the notification → commit state (decision 4). Keep it POSIX-sh simple and
idempotent; a second invocation the same day must be a silent no-op.

### 3. launchd plist — `~/Library/LaunchAgents/ai.targum.agent.plist`

`StartCalendarInterval` daily at the owner's chosen hour;
`StandardOutPath`/`StandardErrorPath` → `logs/launchd.log`; `WorkingDirectory`
= repo root. Note launchd (unlike cron) runs a missed calendar job once on
wake — that's why it was chosen for a laptop. Provide the `launchctl
bootstrap`/`bootout` commands in the run instructions, and keep a copy of the
plist in `scripts/` (the installed file is a copy, not a symlink).

### 4. Supervised rollout

- Before first firing: one **influx-scale supervised SDK run** if the
  worklist is large (use `--limit` to stage it); required by STATUS before
  automation.
- Dry-run the wrapper manually twice: once due (runs), once not-due (silent
  no-op).
- Watch the first 2–3 scheduled firings (check `logs/launchd.log`, the
  notification, and the auto-commit) before trusting it.
- Update `STATUS.md`/`HISTORY.md` when live.

## Verification checklist

- [ ] `python -m tests.test_config` green with the new `run_interval_days` key
- [ ] API path halts at the cap (unit test with a tiny cap + stubbed costs)
- [ ] wrapper: due / not-due / crash paths all notify or no-op correctly
- [ ] Keychain lookup works from a non-interactive shell (`launchctl kickstart`)
- [ ] a full scheduled firing end-to-end: run → summary → notification → commit

## Owner inputs (ask before wiring the schedule)

1. **Notification channel**: macOS notification, email (Resend), or both?
2. **Fire hour** for the daily launchd trigger (e.g. 09:00)?
3. **`run_interval_days` starting value** (7 was floated for summer, 2 for
   semester)?
4. **Auto-push** after the state commit, or commit-only (owner pushes
   manually)?
5. **Keychain item name** to store the OAuth token under (script will document
   the one-time `security add-generic-password` setup either way).
