"""
backfill_figures.py — run the figure pass (core/figures.py) over files that
were translated before it existed.

For each translated manifest entry of the chosen courses: check the source is
unchanged on Drive (md5), re-download it, locate the figures with the
LOCATE-FIGURES session, crop them into <Type>/_figures/<stem>/ and rewrite the
.md's placeholders. The original .md is copied to logs/figure_backups/<run>/
before it is rewritten.

Usage (from the project root, subscription auth as for a manual run):
    .venv/bin/python scripts/backfill_figures.py                 # this semester's courses
    .venv/bin/python scripts/backfill_figures.py --course "Computational Models"
    .venv/bin/python scripts/backfill_figures.py --course "Computational Models" --dry-run
    .venv/bin/python scripts/backfill_figures.py --limit 2 --force

--dry-run runs the locate session and prints the planned crops and the .md
diff, writing nothing. --force re-runs files already done by this pass version
(only placeholders still left are consumed — see core/figures.py).
"""

import argparse
import asyncio
import difflib
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core import config, costs, drive, figures, manifest, vault  # noqa: E402
from core.paths import LOGS_DIR  # noqa: E402
from sdk import sessions  # noqa: E402
from sdk.prepare import sniff_source_kind  # noqa: E402

# Semester 5 (fall 2026-27). Older semesters are opt-in with --course.
DEFAULT_COURSES = [
    "Computational Models",
    "Introduction to Signal Processing",
    "Introduction to Analog Circuits",
    "Systems Programming",
    "Analog Circuits Lab",
]

CONCURRENCY = 2


def _select(entries, courses, force):
    """Translated entries of the chosen courses that still need the pass."""
    todo = []
    for e in entries:
        if e.get("course") not in courses or not e.get("md_path"):
            continue
        if e.get("model") == "skipped_permanent":
            continue
        done = e.get("figures") or {}
        if (not force and done.get("status") in ("done", "none")
                and done.get("pass_version") == figures.PASS_VERSION):
            continue
        todo.append(e)
    return todo


class Run:
    def __init__(self, cfg, dry_run):
        self.cfg = cfg
        self.dry_run = dry_run
        self.vault = Path(cfg.vault_path)
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        LOGS_DIR.mkdir(exist_ok=True)
        self.ledger = LOGS_DIR / f"ledger_figures_{self.run_id}.jsonl"
        self.backups = LOGS_DIR / "figure_backups" / self.run_id
        self.turn = 0
        self.rows = []

    def record(self, result):
        self.turn += 1
        shim = SimpleNamespace(usage=result.usage, model=self.cfg.model)
        self.rows.append(costs.record_call(self.ledger, self.run_id, self.turn,
                                           "figures_backfill", shim,
                                           result.duration_ms or 0))

    async def one(self, entry, sem):
        name = entry["md_path"]
        async with sem:
            md_path = self.vault / entry["md_path"]
            if not md_path.exists():
                return name, "skipped: .md not in the vault"
            if not figures.parse_placeholders(md_path.read_text(encoding="utf-8")):
                if not self.dry_run:
                    vault.record_figures(entry["drive_file_id"],
                                         figures.status_record("none", count=0, unresolved=0))
                return name, "no placeholders"

            fid = entry["drive_file_id"]
            meta = await asyncio.to_thread(drive.file_meta, fid)
            if entry.get("source_md5") and meta.get("md5Checksum") != entry["source_md5"]:
                return name, "skipped: source changed on Drive since translation (re-translate first)"
            data = await asyncio.to_thread(drive.download_bytes, fid)
            kind = sniff_source_kind(data)

            async def locate(placeholders, source_bytes, source_kind):
                located, results = await sessions.locate_figures(
                    placeholders, source_bytes, source_kind,
                    entry.get("drive_file_name", md_path.name), self.cfg.model)
                for r in results:
                    self.record(r)
                return located, None

            if not self.dry_run:
                backup = self.backups / entry["md_path"]
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(md_path, backup)
            try:
                rec = await figures.run_figure_pass(
                    md_path, data, kind, entry.get("chosen_mode") or "unknown", fid,
                    locate, dry_run=self.dry_run)
            except Exception as e:
                if not self.dry_run:
                    vault.record_figures(fid, figures.failed_record(e))
                return name, f"FAILED: {e!r}"
            rec.pop("result", None)
            if self.dry_run:
                self._print_dry(md_path, rec)
            else:
                vault.record_figures(fid, rec)
            return name, f"{rec['status']}: {rec['count']} cropped, {rec['unresolved']} left"

    def _print_dry(self, md_path, rec):
        print(f"\n=== {md_path.relative_to(self.vault)}")
        for idx, page, bbox, fname in rec["plan"]:
            label = f"placeholder {idx}" if idx is not None else "unreferenced"
            print(f"  {fname}  p.{page}  bbox={bbox}  ← {label}")
        old = md_path.read_text(encoding="utf-8").splitlines()
        new = rec["markdown"].splitlines()
        sys.stdout.writelines(line + "\n" for line in difflib.unified_diff(
            old, new, "before", "after", n=1, lineterm=""))


async def _amain(args):
    cfg = config.load_config()
    run = Run(cfg, args.dry_run)
    todo = _select(manifest.load_log(), set(args.course or DEFAULT_COURSES), args.force)
    if args.limit is not None:
        todo = todo[:args.limit]
    print(f"{len(todo)} file(s) to process"
          f"{' (dry run — nothing is written)' if args.dry_run else ''}.")
    sem = asyncio.Semaphore(CONCURRENCY)
    results = await asyncio.gather(*(run.one(e, sem) for e in todo))
    print("\nSummary:")
    for name, outcome in results:
        print(f"  {outcome:55}  {name}")
    tokens = sum((r.get("input_tokens") or 0) + (r.get("output_tokens") or 0)
                 for r in run.rows if isinstance(r, dict))
    print(f"\n{run.turn} locate session(s), ~{tokens:,} tokens. Ledger: {run.ledger}")
    if not args.dry_run and todo:
        print(f"Backups of the rewritten .md files: {run.backups}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Crop and embed figures in already-translated files")
    parser.add_argument("--course", action="append",
                        help="English course name (repeatable); default: this semester's courses")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    # Subscription billing guard, as in sdk/agent_sdk.py main().
    if os.environ.pop("ANTHROPIC_API_KEY", None):
        print("NOTE: ANTHROPIC_API_KEY removed from env (subscription mode).")
    asyncio.run(_amain(args))


if __name__ == "__main__":
    main()
