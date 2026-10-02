# Unattended runs — launchd automation (Task 2)

*Built 2026-07-30 per `archive/task2_launchd_prompt.md`. Owner decisions: macOS
notifications (changed from email 2026-07-30 — no external service, no extra
secret), daily trigger at 18:00, `run_interval_days: 3`, commit-only (no
auto-push), Keychain item `targum-claude-oauth`.*

## How it works

launchd fires `scripts/run_agent.sh` **daily at 18:00** (once on wake if the
Mac was asleep at 18:00). The wrapper then:

1. **Due-check** — reads `run_interval_days` from `config.json` and compares
   against the `logs/last_run` epoch stamp. Not due → silent exit (one
   `not due` line in `logs/launchd.log`). Changing cadence = editing the one
   number in `config.json`; the plist is never touched.
2. **Auth** — exports `CLAUDE_CODE_OAUTH_TOKEN` from the macOS Keychain
   (launchd sessions don't inherit your shell's Claude login). Missing token →
   email alert + abort *without* stamping, so it retries (and re-alerts) daily
   until fixed.
3. **Network wait** — a wake-triggered firing can start before Wi-Fi
   re-associates (this crashed the 2026-08-02 and 2026-08-06 runs on DNS).
   The wrapper polls DNS for up to 3 minutes; still down → alert + abort
   *without* stamping, so it retries daily.
4. **Run** — `.venv/bin/python -m sdk.agent_sdk` (or `-m legacy.agent` when
   `auth_mode: "api"`), output appended to `logs/launchd.log`.
5. **Stamp** — writes `logs/last_run` only when the agent completed its work
   loop (its log contains `RUN SUMMARY`) or exited 0: transiently-failed files
   stay unrecorded in the manifest and are retried on the *next due run*. A
   run that crashed *before* the summary does **not** stamp — it stays due and
   the whole run retries tomorrow instead of silently losing the interval.
6. **Notify** — fires a macOS notification titled
   `Targum run <run_id>: OK | DEGRADED | CRASHED` with the RUN SUMMARY counts
   (translated / skips / already_done / transient errors / cost / wall-clock).
   Exit 1 *with* a RUN SUMMARY = DEGRADED (transient errors / unresolved
   events); exit 1 *without* one = CRASHED (unhandled exception). macOS
   truncates the body — the full summary is always in
   `logs/agent_sdk_<run>.log` and `logs/launchd.log`.
7. **Commit** — commits `translated_log.json` (+ `courses.json` when changed)
   as `state: unattended run <run_id>`. **Commit only** — push manually.

## One-time setup

### 1. Claude OAuth token → Keychain

```sh
claude setup-token          # prints a long-lived subscription token
security add-generic-password -a "$USER" -s targum-claude-oauth -w '<paste token>'
# verify (must work from a NON-interactive shell too):
security find-generic-password -s targum-claude-oauth -w | head -c 12
```

### 2. Install the LaunchAgent (a copy, not a symlink)

```sh
cp scripts/ai.targum.agent.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.targum.agent.plist
```

Remove / reload after editing the plist:

```sh
launchctl bootout gui/$(id -u)/ai.targum.agent
```

## Testing & supervision

```sh
sh scripts/run_agent.sh                    # manual dry-run (due → runs; not due → silent)
sh scripts/run_agent.sh --force            # bypass the due-check (what the menu bar's "Run now" does)
launchctl kickstart gui/$(id -u)/ai.targum.agent   # fire the installed job now
rm logs/last_run                           # force the next invocation to be "due"
tail -f logs/launchd.log                   # watch a firing
```

Rollout order (per STATUS): one influx-scale **supervised** SDK run first
(`--limit` to stage it), then two manual wrapper dry-runs (due + not-due),
then watch the first 2–3 scheduled firings (launchd.log, the notification,
auto-commit) before trusting it.

## Menu-bar app (`scripts/menubar.py`, launchd `ai.targum.menubar`)

A read-only rumps status app over the wrapper's artefacts - it never touches
the agent. Title `📚` + state: `✅` last run OK, not due yet · `⏳` last run
OK, due - waiting for the next 18:00 firing · `🔄` run in progress
(`run_agent.sh` process alive) · `⚠️` last run DEGRADED · `❌` last run
CRASHED / aborted (token, network, config) / interrupted.

Menu: last run (time, status, run id) with the RUN SUMMARY counts as a
submenu; next run (first daily firing at/after `last_run + run_interval_days`);
**Run every** 1/2/3/5/7/14 days (rewrites *only* `run_interval_days` in
`config.json`, byte-preserving the rest; the wrapper re-reads it each firing);
**Max files per run** unlimited/1/3/5/10/20/50 (writes only `max_files_per_run`;
the wrapper passes it to the SDK agent as `--limit N`, so the rest of the
worklist reappears next run - 0/absent = unlimited; ignored on the legacy
`api` path, which has no `--limit`); **Model** - the Claude model every
session of the next run uses (writes only `model`; Opus 5.5 / Opus 5 /
Sonnet 5 / Fable 5.1 / Haiku 4.5, or "Other model id…"); **Drive folder** - the
folder the agent scans (`root_folder_id`): **Choose folder in Finder…** opens a
native folder picker in the Drive for desktop tree
(`~/Library/CloudStorage/GoogleDrive-*`) and takes the folder's Drive id from
its `com.google.drivefs.item-id#S` xattr (no second confirmation); **Recent
folders** (last 8, kept in `logs/.menubar_recent.json`, including the folder you
just switched away from); plus the current folder's parent, sibling folders
(e.g. another semester), subfolders, or a pasted Drive folder URL/id
(validated as a folder), each with a confirmation dialog; read-only
Drive lookups via `core.drive` (`token.json`), fetched in the background and
cached until "Refresh folder list"; the change applies to the next run, the
manifest is keyed by file id so nothing already translated is lost; **Run now** (confirmation dialog, then `scripts/run_agent.sh --force` - a real,
billable run); Open vault / latest run log / wrapper log; Quit. Refreshes when
`logs/launchd.log` (or `config.json` / `last_run`) changes, checked every 60s.

```sh
.venv/bin/python -m pip install rumps                 # one-time (in requirements.txt)
cp scripts/ai.targum.menubar.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/ai.targum.menubar.plist
launchctl kickstart -k gui/$(id -u)/ai.targum.menubar   # restart after editing menubar.py
.venv/bin/python scripts/menubar.py --print           # dump what the menu shows (no GUI)
```

KeepAlive restarts it if it quits; stderr goes to `logs/menubar.err`.

## Notes

- `spend_cap_usd` applies to the **api** path only (`legacy/agent.py` stops
  cleanly at the cap); the subscription path ignores it.
- The agent exits **1** on a degraded run (transient errors / unresolved
  events), **0** when clean — but Python also exits 1 on any unhandled
  exception, so the wrapper tells the two apart by whether the run log
  contains `RUN SUMMARY` (present → DEGRADED, absent → CRASHED).
- Notifications require them to be allowed for "Script Editor"/"osascript" in
  System Settings → Notifications (macOS asks on first use).
- `logs/` (including `last_run`) is gitignored; the plist in `scripts/` is the
  source of truth, the installed copy in `~/Library/LaunchAgents/` is a copy.
