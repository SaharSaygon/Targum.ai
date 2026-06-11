"""
init_translation_log.py — one-time interactive setup for translated_log.json.

Walks the Google Drive source folder, downloads each PDF to hash it, then
prompts you to pair it with an existing Obsidian .md file or mark it as
not-yet-translated / permanently skipped. Safe to re-run: already-logged
Drive file IDs are skipped automatically.

Usage (from the project root):
    python scripts/init_translation_log.py
    python scripts/init_translation_log.py --course "Design of Algorithms"

Requires:
    .env             OBSIDIAN_VAULT_PATH, DRIVE_SOURCE_FOLDER_ID
    credentials.json Google OAuth client secrets
    courses.json     Hebrew folder name → English course name mapping

Agent contract: entries with model='skipped_permanent' must NEVER be
re-translated or re-prompted by the agent. To un-skip, manually delete
the entry from translated_log.json.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
from datetime import datetime, timezone
from difflib import get_close_matches
from pathlib import Path

import yaml
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# This script lives in scripts/; the modules and data files live in the
# project root one level up.
PROJECT_ROOT = Path(__file__).parent.parent

import sys
sys.path.insert(0, str(PROJECT_ROOT))
from manifest import load_log, save_log

LOG_PATH = PROJECT_ROOT / "translated_log.json"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Keywords in relative path → source_type value
TYPE_KEYWORDS = {
    "lecture": "lecture",
    "lec": "lecture",
    "tutorial": "tutorial",
    "tirgul": "tutorial",
    "homework": "homework",
    "hw": "homework",
    "exam": "exam",
    "moed": "exam",
    "slides": "slides",
}


# ── Drive auth ────────────────────────────────────────────────────────────────

def get_drive_credentials() -> Credentials:
    """Load credentials from token.json, refreshing or re-running OAuth as needed."""
    creds = None
    token_path = PROJECT_ROOT / "token.json"
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(PROJECT_ROOT / "credentials.json"), SCOPES
            )
            creds = flow.run_local_server(port=0)
        with open(token_path, "w", encoding="utf-8") as f:
            f.write(creds.to_json())
    return creds


# ── Drive helpers ─────────────────────────────────────────────────────────────

def list_pdfs_recursive(
    service, folder_id: str, course_name: str | None = None
) -> list[dict]:
    """Return [{id, name, parent_folder_name}, ...] for every PDF under folder_id.

    parent_folder_name is always the top-level course folder name (first level
    below DRIVE_SOURCE_FOLDER_ID), so it maps correctly to courses.json keys.
    """
    results = []
    query = f"'{folder_id}' in parents and trashed = false"
    page_token = None
    while True:
        resp = (
            service.files()
            .list(
                q=query,
                fields="nextPageToken, files(id, name, mimeType)",
                pageToken=page_token,
            )
            .execute()
        )
        for item in resp.get("files", []):
            if item["mimeType"] == "application/vnd.google-apps.folder":
                # First level from root: this item IS the course folder.
                # Deeper levels: inherit the already-established course_name.
                inherited = course_name if course_name else item["name"]
                results.extend(list_pdfs_recursive(service, item["id"], inherited))
            elif item["name"].lower().endswith(".pdf"):
                results.append(
                    {"id": item["id"], "name": item["name"], "parent_folder_name": course_name}
                )
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results


def download_bytes(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def sha256_of(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


# ── Vault helpers ─────────────────────────────────────────────────────────────

def walk_vault_md(vault_path: Path) -> dict[str, Path]:
    """Return {relative_path_str: absolute_Path} for every .md file in vault."""
    return {
        str(p.relative_to(vault_path)): p
        for p in vault_path.rglob("*.md")
    }


# ── Frontmatter parsing / rewriting ───────────────────────────────────────────
#
# YAML frontmatter is delimited by lines containing only "---".
# A file that starts with "---\n" is assumed to have frontmatter.
# We find the closing "---" by scanning for "\n---\n" (or "\n---" at EOF).
# Everything before the second delimiter is YAML; everything after is the body.
#
# PyYAML is used for both parsing and serialisation so we never hand-craft YAML
# strings. allow_unicode=True writes Hebrew characters literally rather than as
# \uXXXX escapes. sort_keys=False preserves insertion order.

def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Return (frontmatter_dict_or_None, body_str)."""
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 4)
    if end == -1:
        # Frontmatter closes at the very end of the file (no body)
        if text.endswith("\n---"):
            yaml_str = text[4:-4]
            body = ""
        else:
            return None, text
    else:
        yaml_str = text[4:end]
        body = text[end + 5:]   # skip the "\n---\n" delimiter (5 chars)
    try:
        fm = yaml.safe_load(yaml_str) or {}
    except yaml.YAMLError:
        return None, text
    return fm, body


def build_frontmatter(fields: dict) -> str:
    """Serialise a dict to a YAML frontmatter block (opening and closing ---)."""
    return "---\n" + yaml.dump(fields, allow_unicode=True, sort_keys=False) + "---\n"


# ── Atomic writes ─────────────────────────────────────────────────────────────
#
# Both translated_log.json and vault .md files are written atomically:
#   1. Write the new content to a sibling .tmp file.
#   2. Call os.replace(tmp, final).
#
# os.replace() maps to the OS rename(2) syscall, which is atomic on POSIX —
# the filesystem switches the directory entry in a single operation, so readers
# always see either the old file or the new file, never a partial write.
# On Windows it is also atomic as of Python 3.3 (uses MoveFileEx MOVEFILE_REPLACE_EXISTING).

def atomic_write_text(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


# load_log / save_log now live in manifest.py (imported above).


# ── Type inference ────────────────────────────────────────────────────────────

def infer_type_from_path(rel_path_str: str) -> str | None:
    lower = rel_path_str.lower()
    for keyword, type_val in TYPE_KEYWORDS.items():
        if keyword in lower:
            return type_val
    return None


def prompt_type() -> str:
    options = ["lecture", "tutorial", "homework", "exam", "slides"]
    print("  Could not infer type from path. Choose:")
    for i, opt in enumerate(options, 1):
        print(f"    [{i}] {opt}")
    while True:
        choice = input("  > ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(options):
            return options[int(choice) - 1]
        print("  Invalid, try again.")


# ── Fuzzy matching ────────────────────────────────────────────────────────────
#
# difflib.get_close_matches(word, possibilities, n, cutoff) works by computing
# a SequenceMatcher ratio for every (word, candidate) pair and returning the
# top-n pairs whose ratio >= cutoff. The ratio is 2*M / T, where M is the
# number of matching characters and T is the total characters in both strings.
#
# We normalise stems before matching:
#   - lower-case everything
#   - strip "_EN" / "_en" suffixes that agents append
#   - collapse runs of separators (_, -, space) into a single space
#
# The course filter is applied first (only .md files inside the matching
# Obsidian course folder are considered) so the difflib pool stays small
# and the ratio isn't diluted by completely unrelated files.

def _normalize(s: str) -> str:
    s = re.sub(r"_[0-9a-f]{16,}$", "", s, flags=re.IGNORECASE)  # strip Drive hash suffix
    s = re.sub(r"_en$", "", s.lower().strip(), flags=re.IGNORECASE)
    return re.sub(r"[_\-\s]+", " ", s).strip()


def fuzzy_match_candidates(
    drive_stem: str,
    md_index: dict[str, Path],
    course_en: str | None,
    n: int = 3,
) -> list[str]:
    """Return up to n relative vault paths that are close matches for drive_stem."""
    if course_en:
        pool = {rel: p for rel, p in md_index.items()
                if rel.lower().startswith(course_en.lower())}
    else:
        pool = md_index

    drive_norm = _normalize(drive_stem)
    # Build {normalized_stem: rel_path}; last writer wins on collision (acceptable)
    stem_map: dict[str, str] = {_normalize(Path(rel).stem): rel for rel in pool}

    matches = get_close_matches(drive_norm, stem_map.keys(), n=n, cutoff=0.5)
    return [stem_map[m] for m in matches]


# ── Log entry factories ───────────────────────────────────────────────────────

def _manual_entry(
    file_id: str, file_name: str, content_hash: str,
    rel_path: str, course_en: str | None, file_type: str, today_iso: str,
) -> dict:
    return {
        "drive_file_id": file_id,
        "drive_file_name": file_name,
        "source_content_hash": content_hash,
        "md_path": rel_path,
        "course": course_en,
        "type": file_type,
        "translated_at": today_iso,
        "model": "manual",
        "cost_usd": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }


def _not_yet_entry(file_id: str, file_name: str, content_hash: str) -> dict:
    return {
        "drive_file_id": file_id,
        "drive_file_name": file_name,
        "source_content_hash": content_hash,
        "md_path": None,
        "course": None,
        "type": None,
        "translated_at": None,
        "model": "not_translated_yet",
        "cost_usd": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }


def _skipped_entry(file_id: str, file_name: str, content_hash: str, reason: str) -> dict:
    return {
        "drive_file_id": file_id,
        "drive_file_name": file_name,
        "source_content_hash": content_hash,
        "md_path": None,
        "course": None,
        "type": None,
        "translated_at": None,
        "model": "skipped_permanent",
        "skip_reason": reason,
        "cost_usd": 0,
        "input_tokens": 0,
        "output_tokens": 0,
    }


def _prompt_skip_reason() -> str:
    print("  Skip reason:")
    presets = ["duplicate", "outdated", "not_relevant"]
    for i, opt in enumerate(presets, 1):
        print(f"    [{i}] {opt}")
    print("    [4] other (free text)")
    while True:
        choice = input("  > ").strip()
        if choice in ("1", "2", "3"):
            return presets[int(choice) - 1]
        if choice == "4":
            return input("  Reason: ").strip()
        print("  Invalid, try again.")


# ── Vault .md rewrite ─────────────────────────────────────────────────────────

def rewrite_md(
    vault_path: Path,
    rel_path: str,
    file_id: str,
    file_name: str,
    content_hash: str,
    course_en: str | None,
    file_type: str,
    today_date: str,
) -> None:
    """Inject Drive metadata into the .md frontmatter, preserving any existing fields."""
    abs_path = vault_path / rel_path
    text = abs_path.read_text(encoding="utf-8")
    existing_fm, body = parse_frontmatter(text)

    # Merge: keep existing fields (title, topics, lecture_number, etc.),
    # then overlay the fields we know authoritatively.
    fm: dict = existing_fm or {}
    fm["drive_file_id"] = file_id
    fm["source_content_hash"] = content_hash
    fm["source_file"] = file_name
    fm["date_translated"] = today_date
    fm["source_type"] = file_type
    if course_en and "course" not in fm:
        fm["course"] = course_en

    atomic_write_text(abs_path, build_frontmatter(fm) + body)


# ── Summary ───────────────────────────────────────────────────────────────────

def print_summary(total: int, counts: dict, skipped_permanently: list[tuple]) -> None:
    print()
    print("=" * 52)
    print("Initialization complete.")
    print(f"  Total Drive files:              {total}")
    print(f"  Paired with manual translation: {counts['paired']}")
    print(f"  Marked not-yet-translated:      {counts['not_yet']}")
    print(f"  Marked permanently skipped:     {counts['skipped']}")
    if skipped_permanently:
        for fname, reason in skipped_permanently:
            print(f"    - {fname} ({reason})")
    print(f"  Errors:                         {counts['errors']}")
    print("=" * 52)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Initialise translated_log.json interactively")
    parser.add_argument(
        "--course",
        metavar="NAME",
        help="Process only files whose Drive course folder contains NAME (Hebrew or English, case-insensitive)",
    )
    args = parser.parse_args()

    load_dotenv(PROJECT_ROOT / ".env")
    vault_path = Path(os.environ["OBSIDIAN_VAULT_PATH"])
    folder_id = os.environ["DRIVE_SOURCE_FOLDER_ID"]

    with open(PROJECT_ROOT / "courses.json", encoding="utf-8") as f:
        courses: dict[str, str] = json.load(f)

    print("Authenticating with Google Drive...")
    creds = get_drive_credentials()
    service = build("drive", "v3", credentials=creds)

    print("Listing PDFs in Drive (recursive)...")
    all_pdfs = list_pdfs_recursive(service, folder_id)

    if args.course:
        filter_term = args.course.lower()
        all_pdfs = [
            p for p in all_pdfs
            if p["parent_folder_name"] and (
                filter_term in p["parent_folder_name"].lower()
                or filter_term in (courses.get(p["parent_folder_name"], "")).lower()
            )
        ]
        print(f"Filtered to {len(all_pdfs)} PDFs matching --course {args.course!r}.\n")
    else:
        print(f"Found {len(all_pdfs)} PDFs.\n")

    print("Indexing vault .md files...")
    md_index = walk_vault_md(vault_path)
    print(f"Found {len(md_index)} .md files in vault.\n")

    log = load_log()
    already_logged = {e["drive_file_id"] for e in log}
    unprocessed = [p for p in all_pdfs if p["id"] not in already_logged]
    print(
        f"{len(unprocessed)} files to process "
        f"({len(all_pdfs) - len(unprocessed)} already in log).\n"
    )

    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    today_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    counts = {"paired": 0, "not_yet": 0, "skipped": 0, "errors": 0}
    skipped_permanently: list[tuple[str, str]] = []

    for idx, pdf in enumerate(unprocessed, 1):
        file_id = pdf["id"]
        file_name = pdf["name"]
        parent_heb = pdf["parent_folder_name"]
        course_en = courses.get(parent_heb) if parent_heb else None

        try:
            print(
                f"Downloading [{idx}/{len(unprocessed)}] {file_name} ...",
                end=" ", flush=True,
            )
            data = download_bytes(service, file_id)
            content_hash = sha256_of(data)
            print("done.")
        except Exception as e:
            print(f"\n  ERROR downloading {file_name}: {e}")
            counts["errors"] += 1
            continue

        candidates = fuzzy_match_candidates(
            Path(file_name).stem, md_index, course_en
        )

        print(f"\n{'─' * 60}")
        print(f"File {idx}/{len(unprocessed)}: {file_name}")
        if parent_heb:
            course_label = f" → {course_en}" if course_en else " (not in courses.json)"
            print(f"Parent folder: {parent_heb}{course_label}")
        print(f"Hash: {content_hash[:46]}...")
        print()
        if candidates:
            print("Suggested matches in vault:")
            for i, rel in enumerate(candidates, 1):
                print(f"  [{i}] {rel}")
        else:
            print("No close matches found in vault.")
        print()
        print("Action: [1–3] confirm match  [path] custom path  "
              "[s] not yet  [x] permanent skip  [q] save & quit")

        while True:
            choice = input("> ").strip()

            if choice.lower() == "q":
                save_log(log)
                print(
                    "\nProgress saved. Re-run to continue; "
                    "already-processed Drive file IDs will be skipped."
                )
                print_summary(len(all_pdfs), counts, skipped_permanently)
                return

            if choice.lower() == "s":
                log.append(_not_yet_entry(file_id, file_name, content_hash))
                save_log(log)
                counts["not_yet"] += 1
                break

            if choice.lower() == "x":
                reason = _prompt_skip_reason()
                log.append(_skipped_entry(file_id, file_name, content_hash, reason))
                save_log(log)
                counts["skipped"] += 1
                skipped_permanently.append((file_name, reason))
                print(f"  Permanently skipped: {file_name} (reason: {reason})")
                break

            # Number → pick from candidates; anything else → treat as custom path
            matched_rel: str | None = None
            if choice.isdigit():
                n = int(choice)
                if 1 <= n <= len(candidates):
                    matched_rel = candidates[n - 1]
                else:
                    print("  Number out of range, try again.")
                    continue
            else:
                if (vault_path / choice).exists():
                    matched_rel = choice
                else:
                    print(f"  Not found in vault: {choice!r}. Try again.")
                    continue

            try:
                file_type = infer_type_from_path(matched_rel) or prompt_type()
                rewrite_md(
                    vault_path, matched_rel, file_id, file_name,
                    content_hash, course_en, file_type, today_date,
                )
                log.append(
                    _manual_entry(
                        file_id, file_name, content_hash,
                        matched_rel, course_en, file_type, today_iso,
                    )
                )
                save_log(log)
                counts["paired"] += 1
                print(f"  Paired: {matched_rel}")
            except Exception as e:
                print(f"  ERROR processing match: {e}")
                counts["errors"] += 1
            break

    print_summary(len(all_pdfs), counts, skipped_permanently)


if __name__ == "__main__":
    main()
