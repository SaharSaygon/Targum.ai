"""
courses.py — single owner of courses.json I/O (Hebrew course folder name →
English course name). Mirrors manifest.py's role for translated_log.json.

courses.json is now config the AGENT writes: when it encounters a course folder
with no approved mapping it auto-names the course and persists the choice here
directly — there is NO approval gate (the autonomy reversal removed it). Every
write is the agent committing to a name, so update_mapping's caller logs it
loudly as an audit-trail entry.

Atomic write, UTF-8, ensure_ascii=False — Hebrew keys stay readable on disk.
"""

import json
import os
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
COURSES_PATH = _PROJECT_ROOT / "courses.json"


def load_courses() -> dict:
    """Read courses.json (UTF-8). Flat {hebrew: english}. {} if absent."""
    if COURSES_PATH.exists():
        with open(COURSES_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def update_mapping(hebrew_name: str, english_name: str) -> dict:
    """Add or update one {hebrew_name: english_name} mapping and atomically write
    courses.json back.

    In-place update for an existing key (no duplicate); insert for a new one.
    Atomic: write a sibling .tmp then os.replace() over the real file — a single
    rename(2) on POSIX, so a crash mid-write leaves the old courses.json intact
    (same pattern as manifest.save_log). ensure_ascii=False keeps Hebrew readable.
    """
    courses = load_courses()
    courses[hebrew_name] = english_name
    tmp = COURSES_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(courses, f, ensure_ascii=False, indent=2)
    os.replace(tmp, COURSES_PATH)
    return {"hebrew_name": hebrew_name, "english_name": english_name}
