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
# network (wake-triggered firings can beat Wi-Fi re-association) → model check
# (upgrade claude-agent-sdk only if its bundled CLI doesn't know the model)
# → run agent
# → stamp (only if the agent reached its RUN SUMMARY — a startup crash stays
# due and retries tomorrow) → log → notify (macOS notification; full summary
# stays in the logs) → commit state (commit only, no push).
#
# One-time setup (see Documentation/unattended_runs.md):
#   security add-generic-password -a "$USER" -s targum-claude-oauth -w '<token from `claude setup-token`>'
#
# Usage: run_agent.sh [--force]
#   --force  bypass the due-check (menu-bar "Run now"). launchd never passes
#            it, so scheduled behaviour is unchanged.

set -u

FORCE=0
case "${1:-}" in
    --force) FORCE=1 ;;
    "") ;;
    *) echo "usage: $0 [--force]" >&2; exit 2 ;;
esac

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

# ── 1. config: interval + auth mode + per-run file cap ───────────────────────
# max_files_per_run (menu-bar "Max files per run") is read raw: core.config
# ignores unknown keys, so a bad value degrades to 0 = unlimited instead of
# aborting the run.
if ! CFG="$("$PY" -c 'from core.config import load_config
from core.paths import CONFIG_PATH
import json
c = load_config()
m = json.loads(CONFIG_PATH.read_text(encoding="utf-8")).get("max_files_per_run", 0)
m = m if isinstance(m, int) and not isinstance(m, bool) and m > 0 else 0
print(c.run_interval_days, c.auth_mode, m, c.model)' 2>>"$LAUNCHD_LOG")"; then
    llog "CONFIG ERROR — run aborted before due-check"
    notify "Targum run: CONFIG ERROR" \
        "config.json failed to load; the unattended run aborted. See $LAUNCHD_LOG"
    exit 1
fi
set -- $CFG
INTERVAL_DAYS="$1"
AUTH_MODE="$2"
MAX_FILES="$3"
MODEL="$4"

# ── 2. due-check: silent no-op when not yet due ──────────────────────────────
NOW="$(date +%s)"
if [ "$FORCE" -eq 1 ]; then
    llog "forced run (--force) — due-check bypassed"
elif [ -f "$STAMP" ]; then
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

# ── 4b. SDK model check, upgrade ON DEMAND: claude-agent-sdk bundles its own
#      Claude Code CLI, whose model catalog gates which models work. The last
#      verified "<sdk version> <model>" pair is cached in $SDK_OK, so the
#      steady state costs nothing. When the pair changes (new model picked in
#      the menu bar, SDK changed), a one-line probe runs the configured model;
#      only a probe the CLI flags [claude-code:unrecognized_model] triggers a
#      pip upgrade, which is re-probed and rolled back if it doesn't help.
#      Never blocks the run: any other probe failure (network, auth) is logged
#      and the run proceeds without caching ─────────────────────────────────
SDK_OK="$LOGS_DIR/.sdk_model_ok"
sdk_ver() { "$PY" -c 'from importlib.metadata import version; print(version("claude-agent-sdk"))' 2>/dev/null; }
sdk_cli() { "$PY" -c 'import claude_agent_sdk, os; print(os.path.join(os.path.dirname(claude_agent_sdk.__file__), "_bundled", "claude"))' 2>/dev/null; }
probe_model() {
    # probe_model → prints the CLI output; exit status = the CLI's. 120s cap
    # (macOS has no `timeout`; perl's alarm kills the exec'd CLI).
    perl -e 'alarm shift; exec @ARGV' 120 "$(sdk_cli)" -p "reply with exactly: ok" \
        --model "$MODEL" --max-turns 1 2>&1
}
if [ "$MODULE" = "sdk.agent_sdk" ]; then
    OLD_SDK="$(sdk_ver)"
    if [ "$(cat "$SDK_OK" 2>/dev/null)" != "$OLD_SDK $MODEL" ]; then
        OUT="$(probe_model)"; RC=$?
        if printf '%s' "$OUT" | grep -q 'unrecognized_model'; then
            llog "model $MODEL unknown to claude-agent-sdk $OLD_SDK — upgrading"
            if "$PY" -m pip install -q --upgrade --disable-pip-version-check --timeout 30 \
                    claude-agent-sdk >> "$LAUNCHD_LOG" 2>&1; then
                NEW_SDK="$(sdk_ver)"
                if [ "$NEW_SDK" = "$OLD_SDK" ]; then
                    llog "no newer claude-agent-sdk than $OLD_SDK — model $MODEL stays unrecognized"
                    notify "Targum: model not recognized" \
                        "$MODEL is unknown even to the latest claude-agent-sdk ($OLD_SDK). Check the model id in the menu bar."
                else
                    OUT="$(probe_model)"; RC=$?
                    if [ "$RC" -eq 0 ] && ! printf '%s' "$OUT" | grep -q 'unrecognized_model'; then
                        llog "claude-agent-sdk upgraded $OLD_SDK → $NEW_SDK (model $MODEL now OK)"
                        echo "$NEW_SDK $MODEL" > "$SDK_OK"
                    else
                        llog "claude-agent-sdk $NEW_SDK did not fix model $MODEL — rolling back to $OLD_SDK"
                        printf '%s\n' "$OUT" | tail -3 >> "$LAUNCHD_LOG"
                        "$PY" -m pip install -q --disable-pip-version-check \
                            "claude-agent-sdk==$OLD_SDK" >> "$LAUNCHD_LOG" 2>&1 \
                            || llog "ROLLBACK FAILED — fix .venv manually"
                        notify "Targum: model not recognized" \
                            "$MODEL fails even on claude-agent-sdk $NEW_SDK (rolled back). Check the model id in the menu bar."
                    fi
                fi
            else
                llog "claude-agent-sdk upgrade failed — continuing on ${OLD_SDK:-unknown}"
            fi
        elif [ "$RC" -eq 0 ]; then
            llog "model check OK: $MODEL on claude-agent-sdk $OLD_SDK"
            echo "$OLD_SDK $MODEL" > "$SDK_OK"
        else
            llog "model check inconclusive (exit $RC, not a catalog issue) — continuing, will re-check next run"
            printf '%s\n' "$OUT" | tail -3 >> "$LAUNCHD_LOG"
        fi
    fi
fi

# ── 5. run ───────────────────────────────────────────────────────────────────
# max_files_per_run → the SDK agent's --limit (the rest of the worklist
# reappears next run); the legacy agent has no such flag.
LIMIT_ARGS=""
LIMIT_NOTE=""
if [ "$MAX_FILES" -gt 0 ]; then
    if [ "$MODULE" = "sdk.agent_sdk" ]; then
        LIMIT_ARGS="--limit $MAX_FILES"
        LIMIT_NOTE=", max_files=$MAX_FILES"
    else
        LIMIT_NOTE=", max_files=$MAX_FILES IGNORED (legacy path has no --limit)"
    fi
fi
llog "due → starting $MODULE (auth_mode=$AUTH_MODE, interval=${INTERVAL_DAYS}d${LIMIT_NOTE})"
MARKER="$LOGS_DIR/.run_marker"
# menu-bar "Kill run" drops this flag right before signalling the agent, so
# the outcome below reads KILLED instead of CRASHED; clear any stale one
KILLED_FLAG="$LOGS_DIR/.killed"
rm -f "$KILLED_FLAG"
: > "$MARKER"
# $LIMIT_ARGS is intentionally unquoted (word-split into "--limit N")
"$PY" -m "$MODULE" $LIMIT_ARGS >> "$LAUNCHD_LOG" 2>&1
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

KILLED=0
[ -f "$KILLED_FLAG" ] && KILLED=1
rm -f "$KILLED_FLAG"

case "$EXIT" in
    0) STATUS="OK" ;;
    1) if [ "$HAVE_SUMMARY" -eq 1 ]; then STATUS="DEGRADED"; else STATUS="CRASHED (exit 1)"; fi ;;
    *) STATUS="CRASHED (exit $EXIT)" ;;
esac
[ "$KILLED" -eq 1 ] && STATUS="KILLED (from menu bar)"

# stamp only a run that did its work (possibly degraded — those files retry on
# the next due run); a startup crash leaves the interval unconsumed so the
# whole run retries tomorrow instead of silently losing ${INTERVAL_DAYS} days
if [ "$EXIT" -eq 0 ] || [ "$HAVE_SUMMARY" -eq 1 ]; then
    echo "$NOW" > "$STAMP"
else
    if [ "$KILLED" -eq 1 ]; then
        llog "run $RUN_ID killed from menu bar — last_run NOT stamped, retries tomorrow"
    else
        llog "run $RUN_ID crashed before RUN SUMMARY — last_run NOT stamped, retries tomorrow"
    fi
fi
llog "run $RUN_ID finished: $STATUS"

if [ "$HAVE_SUMMARY" -eq 1 ]; then
    # count lines only (they use ' : '; per-file sub-items start with '   - ')
    # — a macOS notification truncates anyway; the full block is in $RUN_LOG.
    SUMMARY="$(awk '/RUN SUMMARY/{found=1} found && / : /' "$RUN_LOG" \
               | sed 's/  */ /g' | paste -sd ';' -)"
else
    if [ "$KILLED" -eq 1 ]; then
        SUMMARY="Run killed from the menu bar — unfinished files are re-offered next run"
    else
        SUMMARY="Run crashed before printing a summary — see ${RUN_LOG:-$LAUNCHD_LOG}"
    fi
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
