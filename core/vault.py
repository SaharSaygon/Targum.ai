"""
vault.py — vault output paths, skill loading, and the save-to-vault executor.

No LLM calls live here. Split out of translation_engine.py (2026-07-29 reorg)
because both versions of the agent share it: the legacy API engine and the SDK
subscription path write the same vault and record the same manifest.
"""

import os
from datetime import datetime, timezone
from pathlib import Path

from core import manifest
from core.paths import SKILLS_DIR


def load_skill(name: str) -> str:
    """Read a skill file from skills/ by name (UTF-8, repo-root-anchored).

    Each translation tool's system prompt is translate-shared.md concatenated
    with that tool's own mode skill — see text_system_prompt / image_system_prompt.
    """
    return (SKILLS_DIR / name).read_text(encoding="utf-8")


# Skills are loaded PER CALL (per-invocation), NOT once at import — the locked
# design decision (HISTORY "Day 2 redesign": "Each translation tool loads its
# skill every call ... lets skill content evolve without restarts"). The skill
# files are the craft layer; editing one must take effect on the next translation
# without restarting the process. Each mode's system prompt = the shared
# translation logic + that mode's skill, concatenated shared-FIRST (the mode
# skill references shared by path, so shared must sit above it in context).

def text_system_prompt() -> str:
    """Build the text-mode system prompt fresh on every call."""
    return load_skill("translate-shared.md") + "\n\n" + load_skill("translate-text-pdf.md")


def image_system_prompt() -> str:
    """Build the image-mode system prompt fresh on every call."""
    return load_skill("translate-shared.md") + "\n\n" + load_skill("translate-image-pdf.md")


# Maps the semantic type value to the subfolder name inside the course folder:
# "lecture" → <vault>/<course>/Lectures/<file>_EN.md, etc. "reference" maps to
# "" and lands directly in the course root.
TYPE_TO_FOLDER = {
    "lecture":   "Lectures",
    "tutorial":  "Tutorials",
    "homework":  "Homework",
    "exam":      "Exams",
    "reference": "",   # saved directly in the course root folder
}


def vault_output_path(
    vault_path: Path,
    course_english: str,
    type_value: str,
    drive_filename: str,
    custom_subfolder: str | None = None,
) -> Path:
    """Build the Obsidian output path for a translated file:

        <vault>/<course>/<type-subfolder>/<stem>_EN.md

    Subfolder resolution order:
      1. type_value in TYPE_TO_FOLDER → the mapped folder (lecture → Lectures, …;
         "reference" → "" → straight into the course root). Casing-guaranteed
         path for the four standard types.
      2. else if custom_subfolder is given → use it verbatim as the subfolder
         (escape hatch for one-off categories the standard types don't cover).
      3. else (unknown type, no custom_subfolder) → raise ValueError, so a
         garbled type still fails loudly rather than silently dumping to root.

    Path.stem strips the extension: "הרצאה 4.pdf" → "הרצאה 4".

    Pure path construction — does not touch the filesystem; the caller creates
    parent dirs and writes.
    """
    if type_value in TYPE_TO_FOLDER:
        subfolder = TYPE_TO_FOLDER[type_value]
    elif custom_subfolder is not None:
        subfolder = custom_subfolder
    else:
        raise ValueError(
            f"unknown type {type_value!r}; expected one of "
            f"{list(TYPE_TO_FOLDER)} or pass custom_subfolder="
        )

    stem = Path(drive_filename).stem
    if subfolder:
        return vault_path / course_english / subfolder / f"{stem}_EN.md"
    return vault_path / course_english / f"{stem}_EN.md"


def save_to_vault(
    course_english: str,
    type_value: str,
    markdown: str,
    drive_file_id: str,
    drive_filename: str,
    source_hash: str,
    cost_data: dict,                 # {"model","cost_usd","input_tokens","output_tokens"}
    chosen_mode: str,                # "text" | "image"
    mode_reasoning: str,
    vault_path: Path,
    detection_signals: dict | None = None,
    source_md5: str | None = None,   # Drive md5Checksum — powers read_file's freshness gate
    custom_subfolder: str | None = None,
) -> dict:
    """Write the translated .md into the vault and record it in the manifest.

    Executor for a routing decision already made (course + type); it does not
    re-decide where the file goes. No LLM call. See skills/save-to-vault.md.

    Order is load-bearing: the .md is fully written and atomically renamed BEFORE
    the manifest is touched, so a failed disk write never leaves a manifest record
    for a file that isn't on disk. All manifest I/O is delegated to manifest.py.
    """
    # 1. Path — vault_output_path owns the type→folder mapping and the
    #    custom_subfolder escape hatch. Its ValueError (unknown type with no
    #    custom_subfolder) propagates from here, BEFORE any filesystem write —
    #    so a bad type leaves no .md and never touches the manifest.
    target = vault_output_path(
        vault_path, course_english, type_value, drive_filename,
        custom_subfolder=custom_subfolder,
    )

    # 2. Atomic .md write — temp file in the SAME directory as the target (same
    #    filesystem, so os.replace is a true atomic rename), UTF-8. Fully written
    #    and renamed before the manifest is touched.
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(markdown, encoding="utf-8")
    os.replace(tmp, target)

    md_path_relative = str(target.relative_to(vault_path))

    # 3. Manifest — only after the .md is safely on disk.
    record_translation(
        drive_file_id=drive_file_id,
        drive_filename=drive_filename,
        source_hash=source_hash,
        md_path_relative=md_path_relative,
        course_english=course_english,
        type_value=type_value,
        cost_data=cost_data,
        chosen_mode=chosen_mode,
        mode_reasoning=mode_reasoning,
        detection_signals=detection_signals,
        source_md5=source_md5,
    )

    return {"status": "saved", "md_path": md_path_relative}


def record_translation(
    drive_file_id: str,
    drive_filename: str,
    source_hash: str,
    md_path_relative: str,
    course_english: str,
    type_value: str,
    cost_data: dict,
    chosen_mode: str,
    mode_reasoning: str,
    detection_signals: dict | None = None,
    source_md5: str | None = None,
    source_modified_time: str | None = None,
) -> dict:
    """Upsert a translated-file manifest entry. The single owner of the entry
    schema — used by save_to_vault (legacy path, which also writes the .md) and
    by the SDK path (where the translate session already Wrote the .md and code
    only records). upsert_entry matches by drive_file_id and replaces in place.
    """
    entry = {
        "drive_file_id":       drive_file_id,
        "drive_file_name":     drive_filename,
        "source_content_hash": source_hash,
        "md_path":             md_path_relative,
        # source_md5 added below only when present (binary files have it; native
        # Google Docs don't) — keeps the field an honest signal for the gate.
        "course":              course_english,
        "type":                type_value,
        "translated_at":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model":               cost_data["model"],
        "cost_usd":            cost_data["cost_usd"],
        "input_tokens":        cost_data["input_tokens"],
        "output_tokens":       cost_data["output_tokens"],
        "chosen_mode":         chosen_mode,
        "mode_reasoning":      mode_reasoning,
    }
    # source_md5, source_modified_time, and detection signals are written only
    # when present — no null keys, so an entry's lack of either is honest
    # absence. modified_time matters for native Google files (no md5): it is
    # what dedup.modified_unchanged gates on in the pre-pass.
    if source_md5 is not None:
        entry["source_md5"] = source_md5
    if source_modified_time is not None:
        entry["source_modified_time"] = source_modified_time
    if detection_signals is not None:
        entry.update(detection_signals)

    entries = manifest.load_log()
    entries = manifest.upsert_entry(entries, entry)
    manifest.save_log(entries)
    return entry


def record_skip(
    drive_file_id: str,
    drive_filename: str,
    source_hash: str,
    skip_reason: str,
    source_md5: str | None = None,
    source_modified_time: str | None = None,
) -> dict:
    """Upsert a skipped_permanent manifest entry (deliberate, rule-based skip).

    Mirrors legacy handle_skip_file's entry shape: a translated entry with the
    translation fields nulled/zeroed. source_md5 when present lets the pre-pass
    md5-skip the file next run; without it the loop still dedups by content hash.
    """
    entry = {
        "drive_file_id":       drive_file_id,
        "drive_file_name":     drive_filename,
        "source_content_hash": source_hash,
        "md_path":             None,
        "course":              None,
        "type":                None,
        "translated_at":       None,
        "model":               "skipped_permanent",
        "skip_reason":         skip_reason,
        "cost_usd":            0,
        "input_tokens":        0,
        "output_tokens":       0,
    }
    if source_md5 is not None:
        entry["source_md5"] = source_md5
    if source_modified_time is not None:
        entry["source_modified_time"] = source_modified_time
    entries = manifest.load_log()
    entries = manifest.upsert_entry(entries, entry)
    manifest.save_log(entries)
    return entry
