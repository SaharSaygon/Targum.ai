#!/bin/sh
# run_agent.sh — unattended-run wrapper for the Targum agent (Task 2).
#
# launchd (ai.targum.agent.plist) invokes this DAILY at the configured hour;
# this script decides whether a run is actually due (run_interval_days in
# config.json vs the logs/last_run stamp) and exits silently when it isn't —
# cadence changes are a config edit, never a launchctl reload. Idempotent: a
# second invocation the same day is a silent no-op.
#
# Sequence: due-check → cd repo root → OAuth token from Keychain → wait for
# network (wake-triggered firings can beat Wi-Fi re-association) → run agent
# → stamp (only if the agent reached its RUN SUMMARY — a startup crash stays
# due and retries tomorrow) → log → notify (macOS notification; full summary
# stays in the logs) → commit state (commit only, no push).
#
# One-time setup (see Documentation/unattended_runs.md):
#   security add-generic-password -a "$USER" -s targum-claude-oauth -w '<token from `claude setup-token`>'

set -u

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

PY="$REPO_ROOT/.venv/bin/python"
LOGS_DIR="$REPO_ROOT/logs"
STAMP="$LOGS_DIR/last_run"
LAUNCHD_LOG="$LOGS_DIR/launchd.log"
KEYCHAIN_OAUTH="targum-claude-oauth"

mkdir -p "$LOGS_DIR"

llog() {
    # one-line audit trail of every wrapper decision, even silent no-ops
    echo "$(date '+%Y-%m-%d %H:%M:%S') wrapper: $*" >> "$LAUNCHD_LOG"
}

notify() {
    # notify <title> <body> — macOS notification. Body is passed as an
    # argument (not interpolated into the AppleScript) so quotes/Hebrew in
    # skip reasons can't break the osascript syntax; macOS truncates long
    # bodies, the full summary always lives in the run log.
    if osascript -e 'on run argv' \
                 -e 'display notification (item 2 of argv) with title (item 1 of argv)' \
                 -e 'end run' -- "$1" "$2" >/dev/null 2>&1; then
        llog "notified: $1"
    else
        llog "NOTIFICATION FAILED: $1"
    fi
}

# ── 1. config: interval + auth mode ──────────────────────────────────────────
if ! CFG="$("$PY" -c 'from core.config import load_config
c = load_config(); print(c.run_interval_days, c.auth_mode)' 2>>"$LAUNCHD_LOG")"; then
    llog "CONFIG ERROR — run aborted before due-check"
    notify "Targum run: CONFIG ERROR" \
        "config.json failed to load; the unattended run aborted. See $LAUNCHD_LOG"
    exit 1
fi
INTERVAL_DAYS="${CFG%% *}"
AUTH_MODE="${CFG##* }"

# ── 2. due-check: silent no-op when not yet due ──────────────────────────────
NOW="$(date +%s)"
if [ -f "$STAMP" ]; then
    LAST="$(cat "$STAMP" 2>/dev/null || echo 0)"
    case "$LAST" in *[!0-9]*|"") LAST=0;; esac
    ELAPSED=$((NOW - LAST))
    if [ "$ELAPSED" -lt $((INTERVAL_DAYS * 86400)) ]; then
        llog "not due (interval ${INTERVAL_DAYS}d, elapsed $((ELAPSED / 3600))h) — no-op"
        exit 0
    fi
fi

# ── 3. auth: OAuth token from Keychain (launchd sessions don't inherit the
#      user's shell login state) ─────────────────────────────────────────────
if [ "$AUTH_MODE" = "subscription" ]; then
    TOKEN="$(security find-generic-password -s "$KEYCHAIN_OAUTH" -w 2>/dev/null || true)"
    if [ -z "$TOKEN" ]; then
        # no stamp written → stays due, re-alerts daily until fixed
        llog "OAUTH TOKEN MISSING from Keychain item '$KEYCHAIN_OAUTH' — run aborted"
        notify "Targum run: TOKEN MISSING" \
            "No Claude OAuth token in Keychain item '$KEYCHAIN_OAUTH'. Run 'claude setup-token' and store it (see Documentation/unattended_runs.md). The run will retry tomorrow."
        exit 1
    fi
    export CLAUDE_CODE_OAUTH_TOKEN="$TOKEN"
    MODULE="sdk.agent_sdk"
    LOG_GLOB="agent_sdk_*.log"
else
    MODULE="legacy.agent"
    LOG_GLOB="agent_[0-9]*.log"
fi

# ── 4. network: a wake-triggered firing can start before Wi-Fi re-associates.
#      Wait for DNS (the same lookup the run's first HTTPS request performs)
#      instead of crashing on it ────────────────────────────────────────────
WAITED=0
until "$PY" -c 'import socket; socket.getaddrinfo("oauth2.googleapis.com", 443)' >/dev/null 2>&1; do
    if [ "$WAITED" -ge 180 ]; then
        # no stamp written → stays due, retries (and re-alerts) daily
        llog "NO NETWORK after ${WAITED}s — run aborted"
        notify "Targum run: NO NETWORK" \
            "DNS still failing after ${WAITED}s. The run aborted without consuming the interval; it retries tomorrow."
        exit 1
    fi
    sleep 10
    WAITED=$((WAITED + 10))
done
[ "$WAITED" -gt 0 ] && llog "network up after ${WAITED}s wait"

# ── 5. run ───────────────────────────────────────────────────────────────────
llog "due → starting $MODULE (auth_mode=$AUTH_MODE, interval=${INTERVAL_DAYS}d)"
MARKER="$LOGS_DIR/.run_marker"
: > "$MARKER"
"$PY" -m "$MODULE" >> "$LAUNCHD_LOG" 2>&1
EXIT=$?

# ── 6. status + stamp + notification ─────────────────────────────────────────
# THIS run's log only (newer than the marker) — falling back to ls -t could
# pick up a previous run's log and mislabel a startup crash as DEGRADED.
RUN_LOG="$(find "$LOGS_DIR" -maxdepth 1 -name "$LOG_GLOB" -newer "$MARKER" 2>/dev/null | head -1)"
rm -f "$MARKER"
RUN_ID="$(basename "${RUN_LOG:-unknown}" .log | sed 's/^agent_sdk_//; s/^agent_//')"

# "RUN SUMMARY" in the log = the agent completed its work loop; without it,
# exit 1 is an unhandled crash, not a degraded-but-finished run
if [ -n "${RUN_LOG:-}" ] && grep -q "RUN SUMMARY" "$RUN_LOG" 2>/dev/null; then
    HAVE_SUMMARY=1
else
    HAVE_SUMMARY=0
fi

case "$EXIT" in
    0) STATUS="OK" ;;
    1) if [ "$HAVE_SUMMARY" -eq 1 ]; then STATUS="DEGRADED"; else STATUS="CRASHED (exit 1)"; fi ;;
    *) STATUS="CRASHED (exit $EXIT)" ;;
esac

# stamp only a run that did its work (possibly degraded — those files retry on
# the next due run); a startup crash leaves the interval unconsumed so the
# whole run retries tomorrow instead of silently losing ${INTERVAL_DAYS} days
if [ "$EXIT" -eq 0 ] || [ "$HAVE_SUMMARY" -eq 1 ]; then
    echo "$NOW" > "$STAMP"
else
    llog "run $RUN_ID crashed before RUN SUMMARY — last_run NOT stamped, retries tomorrow"
fi
llog "run $RUN_ID finished: $STATUS"

if [ "$HAVE_SUMMARY" -eq 1 ]; then
    # count lines only (they use ' : '; per-file sub-items start with '   - ')
    # — a macOS notification truncates anyway; the full block is in $RUN_LOG.
    SUMMARY="$(awk '/RUN SUMMARY/{found=1} found && / : /' "$RUN_LOG" \
               | sed 's/  */ /g' | paste -sd ';' -)"
else
    SUMMARY="Run crashed before printing a summary — see ${RUN_LOG:-$LAUNCHD_LOG}"
fi
notify "Targum run $RUN_ID: $STATUS" "$SUMMARY"

# ── 7. commit state (commit only — owner pushes manually) ────────────────────
if [ -n "$(git status --porcelain -- translated_log.json courses.json)" ]; then
    if git commit -m "state: unattended run $RUN_ID" -- translated_log.json courses.json \
            >> "$LAUNCHD_LOG" 2>&1; then
        llog "state committed (run $RUN_ID)"
    else
        llog "STATE COMMIT FAILED (run $RUN_ID) — commit manually"
    fi
fi

exit "$EXIT"
