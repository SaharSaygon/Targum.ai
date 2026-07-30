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
3. **Run** — `.venv/bin/python -m sdk.agent_sdk` (or `-m legacy.agent` when
   `auth_mode: "api"`), output appended to `logs/launchd.log`.
4. **Stamp** — writes `logs/last_run` once the agent actually started, even if
   it then failed: transiently-failed files stay unrecorded in the manifest and
   are retried on the *next due run*; the notification carries the failure.
5. **Notify** — fires a macOS notification titled
   `Targum run <run_id>: OK | DEGRADED | CRASHED` with the RUN SUMMARY counts
   (translated / skips / already_done / transient errors / cost / wall-clock).
   Exit code 1 from the agent = DEGRADED (transient errors / unresolved
   events). macOS truncates the body — the full summary is always in
   `logs/agent_sdk_<run>.log` and `logs/launchd.log`.
6. **Commit** — commits `translated_log.json` (+ `courses.json` when changed)
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
launchctl kickstart gui/$(id -u)/ai.targum.agent   # fire the installed job now
rm logs/last_run                           # force the next invocation to be "due"
tail -f logs/launchd.log                   # watch a firing
```

Rollout order (per STATUS): one influx-scale **supervised** SDK run first
(`--limit` to stage it), then two manual wrapper dry-runs (due + not-due),
then watch the first 2–3 scheduled firings (launchd.log, the notification,
auto-commit) before trusting it.

## Notes

- `spend_cap_usd` applies to the **api** path only (`legacy/agent.py` stops
  cleanly at the cap); the subscription path ignores it.
- The agent exits **1** on a degraded run (transient errors / unresolved
  events), **0** when clean — visible in `launchd.log` and the notification
  title.
- Notifications require them to be allowed for "Script Editor"/"osascript" in
  System Settings → Notifications (macOS asks on first use).
- `logs/` (including `last_run`) is gitignored; the plist in `scripts/` is the
  source of truth, the installed copy in `~/Library/LaunchAgents/` is a copy.
