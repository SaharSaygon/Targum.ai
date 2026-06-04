"""
manifest.py — single owner of translated_log.json I/O.

Every read/write of the translation manifest goes through here so the manifest's
rules (path resolution, atomic write, in-place upsert, dedup lookup) live in one
place. Consolidated from the duplicated load_log/save_log helpers that previously
lived in translate_one.py, translate_image_pdf.py, and init_translation_log.py.

read_file (dedup reads) and save_to_vault (writes) are expected to use this too.
"""

import hashlib
import json
import os
from pathlib import Path

# The manifest lives next to this module at the project root, matching the path
# the previous per-script helpers resolved (PROJECT_ROOT / "translated_log.json").
_PROJECT_ROOT = Path(__file__).parent
LOG_PATH = _PROJECT_ROOT / "translated_log.json"


def sha256_of(data: bytes) -> str:
    """Canonical content-hash for the manifest's `source_content_hash` field.
    The 'sha256:' prefix is part of the stored value — dedup compares against it
    verbatim, so every producer must use this exact format. (init_translation_log
    defines an identical helper; this is the home for the agent loop's use.)"""
    return "sha256:" + hashlib.sha256(data).hexdigest()


def load_log() -> list[dict]:
    """Read translated_log.json (UTF-8). Returns [] if the file doesn't exist."""
    if LOG_PATH.exists():
        with open(LOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_log(entries: list[dict]) -> None:
    """Atomically write the manifest.

    Write a sibling .tmp file in the same directory, then os.replace() it over
    the real file — a single rename(2) on POSIX, so a crash mid-write leaves the
    old manifest intact. ensure_ascii=False keeps Hebrew readable; indent=2
    matches the on-disk format.
    """
    tmp = LOG_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    os.replace(tmp, LOG_PATH)


def find_by_id(entries: list[dict], drive_file_id: str) -> dict | None:
    """Return the entry with this drive_file_id, or None. Convenience lookup for
    dedup (read_file)."""
    return next(
        (e for e in entries if e.get("drive_file_id") == drive_file_id),
        None,
    )


def upsert_entry(entries: list[dict], entry: dict) -> list[dict]:
    """Insert or replace `entry` by its drive_file_id, IN PLACE.

    If an entry with the same drive_file_id exists, overwrite it at the SAME list
    index (order preserved — not remove-then-append); otherwise append. Mutates
    and returns the same list.
    """
    fid = entry["drive_file_id"]
    for i, existing in enumerate(entries):
        if existing.get("drive_file_id") == fid:
            entries[i] = entry
            return entries
    entries.append(entry)
    return entries
