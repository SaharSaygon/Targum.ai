"""
agent_sdk.py — the subscription-path entry point (Shape D orchestrator).

Python owns the loop; the model owns the judgment. Per worklist file:

    prepare (code: md5 gate → download → dedup → signals, in a thread)
      → ROUTE session   (model: skip vs translate + mode/course/type)
      → TRANSLATE session (model: translate + Write .md to the vault)
      → record (code: manifest entry — cannot be forgotten)

3–4 files run concurrently (config.concurrency); the manifest checkpoint is
per-file, so an interrupted run just resumes on the next invocation (the
pre-pass hands back only what isn't recorded). Refusals and failed text
extraction are fed BACK to a route session so the model — not code — decides
the retry (image mode) or the skip, mirroring the legacy refusal path.

Auth: runs the Claude Code harness on the owner's Claude subscription
(CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`, or an existing Claude Code
login). ANTHROPIC_API_KEY is removed from the child environment in
subscription mode so a stray key can never silently flip billing to the API.

Run from the repo root:  .venv/bin/python -m sdk.agent_sdk
"""

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core import config, costs, courses, prepass, vault
from core.paths import LOGS_DIR
from sdk import sessions
from sdk.prepare import prepare_file

# Set by main() before the loop starts.
CONFIG = None
LOG = None            # append-a-line callable
LEDGER_PATH = None
RUN_ID = None
_session_counter = 0  # ledger turn index (monotonic across concurrent tasks)


def _record_session(category: str, result, model: str):
    """Append one advisory ledger row for a finished session. Under
    subscription the dollars are notional (the plan absorbs them); tokens are
    the real signal for window budgeting."""
    global _session_counter
    _session_counter += 1
    shim = SimpleNamespace(usage=result.usage, model=model)
    return costs.record_call(
        LEDGER_PATH, RUN_ID, _session_counter, category, shim,
        result.duration_ms or 0,
    )


def _courses_block() -> str:
    mapping = courses.load_courses()
    if not mapping:
        return "(none yet)"
    return "\n".join(f"- {h} → {e}" for h, e in mapping.items())


async def _resolve_new_courses(worklist, model):
    """Upfront course naming: one session names every unmapped course folder
    BEFORE the fan-out, so concurrent route sessions can't diverge on a new
    course's English name."""
    mapping = courses.load_courses()
    unmapped = sorted({
        item["parent_path"][0]
        for item in worklist
        if item["parent_path"] and item["parent_path"][0] not in mapping
    })
    if not unmapped:
        return []
    LOG(f"COURSE PRE-RESOLUTION: {len(unmapped)} unmapped folder(s): {unmapped}")
    named, result = await sessions.resolve_course_names(
        unmapped, _courses_block(), model)
    _record_session("course_naming", result, model)
    resolved = []
    for hebrew in unmapped:
        english = named.get(hebrew)
        if english:
            courses.update_mapping(hebrew, english)
            LOG(f"AUTO-NAMED: {hebrew} → {english} (pre-resolution session)")
            print(f"AUTO-NAMED: {hebrew} → {english}")
            resolved.append((hebrew, english))
        else:
            LOG(f"WARNING: pre-resolution returned no name for {hebrew}; "
                "route sessions will see it unmapped")
    return resolved


async def _translate_and_record(ctx, decision, summary):
    """Run the TRANSLATE session for a routed file, verify the Write landed,
    record the manifest entry. Returns True when saved."""
    vault_path = Path(CONFIG.vault_path)
    file_type = decision["file_type"]
    target = vault.vault_output_path(
        vault_path, decision["course_english"], file_type, ctx.name,
        custom_subfolder=decision.get("custom_subfolder"),
    )
    mode = decision["mode"]
    status, result = await sessions.translate_file(
        ctx, mode, decision["course_english"], target, CONFIG.model,
        unofficial_solution=bool(decision.get("unofficial_solution")))
    if result is not None:
        _record_session(f"translation_{mode}", result, CONFIG.model)

    if status != "saved":
        LOG(f"  translate[{ctx.path}] → {status}")
        summary["events"].append((ctx.name, status))
        return status  # "refused: ..." | "text_extraction_failed: ..."

    if not target.exists() or target.stat().st_size == 0:
        raise RuntimeError(
            f"translate session reported SAVED but {target} is missing/empty")

    usage = result.usage or {}
    vault.record_translation(
        drive_file_id=ctx.file_id,
        drive_filename=ctx.name,
        source_hash=ctx.source_hash,
        md_path_relative=str(target.relative_to(vault_path)),
        course_english=decision["course_english"],
        type_value=file_type if file_type in vault.TYPE_TO_FOLDER
                   else (decision.get("custom_subfolder") or file_type),
        cost_data={
            "model":         f"subscription:{CONFIG.model}",
            "cost_usd":      round(result.total_cost_usd or 0.0, 6),
            "input_tokens":  usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
        },
        chosen_mode=mode,
        mode_reasoning=decision["reasoning"],
        detection_signals=ctx.signals,
        source_md5=ctx.drive_md5,
        source_modified_time=ctx.modified_time if ctx.drive_md5 is None else None,
    )
    LOG(f"  SAVED: {ctx.path} → {target.relative_to(vault_path)} ({mode})")
    summary["saved"].append((ctx.name, str(target.relative_to(vault_path))))
    return "saved"


async def _process_file(item, semaphore, summary):
    """The full per-file pipeline. Exceptions are caught per file: one retry,
    then the file is left UNRECORDED so the next run's pre-pass re-offers it."""
    async with semaphore:
        path = "/".join(item["parent_path"] + [item["name"]])
        for attempt in (1, 2):
            try:
                await _process_file_once(item, path, summary)
                return
            except Exception as e:
                LOG(f"  ERROR[{path}] attempt {attempt}: {e!r}")
                if attempt == 2:
                    print(f"  transient failure, left for next run: {path} ({e})")
                    summary["errors"].append((path, str(e)))


async def _process_file_once(item, path, summary):
    # 1. deterministic preparation (blocking I/O → thread)
    prep = await asyncio.to_thread(prepare_file, item)
    status = prep["status"]
    if status == "already_done":
        LOG(f"  already_done: {path} ({prep.get('md_path')})")
        summary["already_done"].append(path)
        return
    if status == "error":
        raise RuntimeError(f"prepare failed: {prep['reason']}")
    ctx = prep["ctx"]

    # 2. ROUTE session — the model decides; up to one re-route with feedback
    #    (refusal / failed text extraction), mirroring the legacy recovery path.
    extra_context = None
    for round_ in (1, 2):
        decision, result = await sessions.route_file(
            ctx, _courses_block(), CONFIG.model, extra_context=extra_context)
        _record_session("route", result, CONFIG.model)
        LOG(f"  route[{path}] round {round_}: {json.dumps(decision, ensure_ascii=False)}")

        if decision["action"] == "skip":
            reason = decision.get("skip_reason") or decision["reasoning"]
            if decision.get("permanent") is False:
                # Skip-floor (cannot classify): run-log only, NO manifest entry
                # (ARCHITECTURE §8). Nothing recorded → the pre-pass re-offers
                # the file next run, so a rename/move between runs is enough
                # to get it translated. Not an error: exit status unaffected.
                LOG(f"  UNPROCESSED (skip-floor, not recorded): {path} — {reason}")
                summary["unprocessed"].append((ctx.name, reason))
                return
            vault.record_skip(
                drive_file_id=ctx.file_id,
                drive_filename=ctx.name,
                source_hash=ctx.source_hash,
                skip_reason=reason,
                source_md5=ctx.drive_md5,
                source_modified_time=(
                    ctx.modified_time if ctx.drive_md5 is None else None),
            )
            LOG(f"  SKIPPED (permanent): {path} — {reason}")
            summary["skipped"].append((ctx.name, reason))
            return

        # 3. TRANSLATE session
        missing = [k for k in ("mode", "course_english", "file_type")
                   if not decision.get(k)]
        if missing:
            raise RuntimeError(f"translate decision missing {missing}: {decision}")
        outcome = await _translate_and_record(ctx, decision, summary)
        if outcome == "saved":
            return
        if round_ == 1:
            extra_context = (
                f"The translate attempt in {decision['mode']} mode did not "
                f"complete: {outcome}. Decide the next step per your rules — "
                "typically retry in image mode for a text-mode refusal or "
                "failed extraction, or skip if translation is impossible."
            )
        else:
            # Second round also failed — leave unrecorded for the next run.
            raise RuntimeError(f"unresolved after re-route: {outcome}")


def _empty_summary():
    return {"saved": [], "skipped": [], "unprocessed": [], "already_done": [],
            "errors": [], "events": []}


async def _amain(cfg, root_folder_id, limit=None):
    global CONFIG
    CONFIG = cfg

    print("Running deterministic pre-pass (md5 diff vs manifest)…")
    worklist, total_scanned = await asyncio.to_thread(
        prepass.build_worklist, root_folder_id)
    LOG(f"PRE-PASS: scanned {total_scanned} files; {len(worklist)} new/changed → worklist")
    if limit is not None and len(worklist) > limit:
        LOG(f"LIMIT: processing first {limit} of {len(worklist)} worklist files")
        print(f"--limit {limit}: processing {limit}/{len(worklist)} files; "
              "the rest reappear next run.")
        worklist = worklist[:limit]
    for item in worklist:
        LOG(f"  worklist: {'/'.join(item['parent_path'] + [item['name']])}  ({item['file_id']})")
    print(f"Pre-pass: {len(worklist)}/{total_scanned} file(s) need work.")
    if not worklist:
        print("Nothing to do.")
        return _empty_summary()

    await _resolve_new_courses(worklist, cfg.model)

    summary = _empty_summary()
    semaphore = asyncio.Semaphore(cfg.concurrency)
    await asyncio.gather(*(
        _process_file(item, semaphore, summary) for item in worklist
    ))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description="Targum.ai — subscription (Agent SDK) run")
    parser.add_argument("--auth-mode", choices=["subscription", "api"],
                        help="override config.json auth_mode for this run")
    parser.add_argument("--root-folder-id", help="override config.json root_folder_id")
    parser.add_argument("--limit", type=int, default=None,
                        help="process at most N worklist files (supervised smoke runs); "
                             "the rest stay unrecorded and reappear next run")
    args = parser.parse_args(argv)

    cfg = config.load_config()
    auth_mode = args.auth_mode or cfg.auth_mode
    if auth_mode == "api":
        print("auth_mode=api → use the legacy path: .venv/bin/python -m legacy.agent")
        sys.exit(2)

    # Billing guard: with a stray ANTHROPIC_API_KEY in the environment the CLI
    # would silently bill the API instead of the subscription. Remove it for
    # this process (and thus every spawned session).
    if os.environ.pop("ANTHROPIC_API_KEY", None):
        print("NOTE: ANTHROPIC_API_KEY removed from env (subscription mode).")

    global LOG, LEDGER_PATH, RUN_ID
    run_start = datetime.now(timezone.utc)
    LOGS_DIR.mkdir(exist_ok=True)
    RUN_ID = run_start.strftime("%Y%m%d_%H%M%S")
    log_path = LOGS_DIR / f"agent_sdk_{RUN_ID}.log"
    ledger_path = LOGS_DIR / f"ledger_sdk_{RUN_ID}.jsonl"
    LEDGER_PATH = ledger_path
    log_f = open(log_path, "w", encoding="utf-8")

    def LOG_fn(msg):
        log_f.write(msg + "\n")
        log_f.flush()
    LOG = LOG_fn

    print(f"Logging this run to {log_path}")
    print(f"Token ledger: {ledger_path}")
    LOG(f"RUN {RUN_ID} — model {cfg.model}, auth subscription, "
        f"concurrency {cfg.concurrency}")

    summary = asyncio.run(_amain(cfg, args.root_folder_id or cfg.root_folder_id,
                                 limit=args.limit))

    # ── run summary ───────────────────────────────────────────────────────────
    wall = (datetime.now(timezone.utc) - run_start).total_seconds()
    rows = []
    if ledger_path.exists():
        with open(ledger_path, encoding="utf-8") as f:
            rows = [json.loads(line) for line in f if line.strip()]
    tokens_in = sum(r["input_tokens"] for r in rows)
    tokens_cr = sum(r["cache_read_input_tokens"] for r in rows)
    tokens_cw = sum(r["cache_creation_i_tokens"] for r in rows)
    tokens_out = sum(r["output_tokens"] for r in rows)
    api_equivalent = sum(r["cost_usd"] for r in rows)  # ledger rows price at API rates
    by_cat = Counter(r["category"] for r in rows)

    lines = [
        "================ RUN SUMMARY (subscription) ================",
        f"translated : {len(summary['saved'])}",
        *(f"   - {name} → {md}" for name, md in summary["saved"]),
        f"deliberate skips (perm) : {len(summary['skipped'])}",
        *(f"   - {name}: {reason}" for name, reason in summary["skipped"]),
        f"skip-floor (unprocessed, re-offered next run) : {len(summary['unprocessed'])}",
        *(f"   - {name}: {reason}" for name, reason in summary["unprocessed"]),
        f"already_done (dedup)    : {len(summary['already_done'])}",
        f"unresolved events       : {len(summary['events'])}"
        + ("" if not summary["events"] else " " + str(summary["events"])),
        f"transient errors (retry next run) : {len(summary['errors'])}"
        + ("" if not summary["errors"] else " " + str(summary["errors"])),
        f"sessions by category    : {dict(by_cat)}",
        f"tokens in/cw/cr/out     : {tokens_in}/{tokens_cw}/{tokens_cr}/{tokens_out}",
        f"API-equivalent cost     : ${api_equivalent:.2f} (paid: $0 — subscription)",
        f"wall-clock duration     : {wall:.1f}s",
        "============================================================",
    ]
    for line in lines:
        print(line)
        LOG(line)
    log_f.close()

    # Degraded-run signal for the unattended wrapper / launchd logs: a run that
    # ends with transient errors or unresolved events exits 1 (its files stay
    # unrecorded and retry next run); a clean run exits 0.
    if summary["errors"] or summary["events"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
