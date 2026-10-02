"""Manifest dedup DECISION logic — pure verdict functions, no I/O.

Extracted verbatim from agent.read_file_logic so the same verdicts can be reused
by the future deterministic pre-pass and unit-tested without an Anthropic API key
or Google OAuth. These functions take already-fetched inputs (the manifest entries
list, the Drive file id, Drive's md5, the content hash) and RETURN a verdict dict.
They do NO I/O — no Drive call, no network, no file read, no cache write. The
caller fetches md5/bytes/hash, performs every cache write, and owns the ORDER of
calls (md5_gate BEFORE download, hash_dedup AFTER hashing). This module only
decides.

Verdict shapes are copied byte-for-byte from the original inline branches — same
keys, same values — so behavior is identical.
"""
from core.manifest import find_by_id

# Sentinel meaning "no dedup hit — caller should keep going (download / detect)".
# Never surfaced to the agent: read_file_logic checks for status == "already_done"
# and falls through to the detector on anything else.
PROCEED = {"status": "proceed"}


def md5_gate(entries, drive_file_id, drive_md5):
    """PRE-DOWNLOAD freshness gate (branch a). If this file is already done with a
    stored source_md5 that matches Drive's current md5, the bytes are provably
    unchanged → return the already_done verdict WITHOUT downloading.

    Returns the verdict dict, or None meaning "gate did not fire — caller must
    download". drive_md5 is None for native Google Docs → gate N/A → None (the
    `drive_md5 is not None` guard short-circuits, so None never crashes)."""
    entry = find_by_id(entries, drive_file_id)
    if (drive_md5 is not None and entry is not None
            and entry.get("md_path") and entry.get("source_md5")
            and entry["source_md5"] == drive_md5):
        return {"status": "already_done", "md_path": entry["md_path"]}
    return None


def _renamed(entry, drive_name):
    """True when the caller supplied Drive's current filename and it differs
    from the name stored in the entry — the user renamed the file since it was
    recorded. A rename is the user's only lever on an already-skipped file
    (e.g. adding the לתרגם / "to_translate" override marker), so the skip
    gates treat it as a change and re-offer the file. Name-blind when
    drive_name is None (legacy callers) or the entry predates stored names."""
    stored = entry.get("drive_file_name")
    return bool(drive_name is not None and stored and stored != drive_name)


def skip_unchanged(entries, drive_file_id, drive_md5, drive_name=None):
    """Pre-pass companion to md5_gate, for DELIBERATE skips. True when the manifest
    has a skipped_permanent entry for this file whose stored source_md5 matches
    Drive's current md5 — i.e. the agent already chose NOT to translate these exact
    bytes, so the pre-pass drops the file (no download, no re-evaluation).

    md5_gate can't cover this case: skip entries have md_path=null, and md5_gate
    requires md_path. md5-only, no I/O. drive_md5 is None (native Google Doc) →
    False, so the loop still sees the file. A renamed skipped file (drive_name
    differs from the stored drive_file_name) is NOT unchanged → False, so the
    user can force a re-evaluation by renaming (see _renamed)."""
    entry = find_by_id(entries, drive_file_id)
    return bool(
        drive_md5 is not None and entry is not None
        and entry.get("model") == "skipped_permanent"
        and entry.get("source_md5") == drive_md5
        and not _renamed(entry, drive_name)
    )


def modified_unchanged(entries, drive_file_id, modified_time, drive_name=None):
    """Pre-pass gate for files WITHOUT an md5 (native Google Docs/Sheets/Slides).

    True when this file's manifest entry — translated (md_path) or deliberately
    skipped — stored a source_modified_time equal to Drive's current
    modifiedTime, i.e. the Doc provably hasn't been touched since it was last
    handled. Binary files never use this gate: their md5 is content-derived and
    sync-immune, while modifiedTime churns on synced folders (see
    drive.file_md5). For native files modifiedTime is the only cheap freshness
    signal Drive offers; a spurious churn just falls through to hash dedup,
    which refreshes the stored value (prepare_file). A renamed SKIPPED doc is
    re-offered (same rename lever as skip_unchanged); a renamed translated doc
    stays done."""
    entry = find_by_id(entries, drive_file_id)
    if entry is None or modified_time is None:
        return False
    if entry.get("model") == "skipped_permanent" and _renamed(entry, drive_name):
        return False
    return bool(
        (entry.get("md_path") or entry.get("model") == "skipped_permanent")
        and entry.get("source_modified_time") == modified_time
    )


def hash_dedup(entries, drive_file_id, source_hash, drive_name=None):
    """POST-DOWNLOAD content dedup. Returns an already_done verdict or PROCEED.

    Branch b — by drive_file_id, GATED on the entry's stored hash matching
    source_hash: md_path present → already_done; model == "skipped_permanent" →
    already_done with the skip reason; model == "not_translated_yet" (or any
    other) → fall through to branch c.

    Branch c — cross-ID fallback: the SAME content already translated under ANY
    other id (a flaky re-download that changed the id, or a true duplicate living
    in two folders). Safe only because the caller integrity-checks the downloaded
    bytes before hashing — without that, this could lock in a truncated
    translation.

    No hit in either branch → PROCEED (caller runs the detector and translates).
    A skipped_permanent entry whose file was RENAMED since (drive_name given and
    ≠ stored drive_file_name) does not count as a hit — the rename is the user's
    signal to re-evaluate (see _renamed)."""
    # branch b — by drive_file_id, gated on hash match
    entry = find_by_id(entries, drive_file_id)
    if entry is not None and entry.get("source_content_hash") == source_hash:
        if entry.get("md_path"):
            # already translated (manual or by a prior run)
            return {"status": "already_done", "md_path": entry["md_path"]}
        if (entry.get("model") == "skipped_permanent"
                and not _renamed(entry, drive_name)):
            return {"status": "already_done",
                    "reason": entry.get("skip_reason", "skipped_permanent")}
        # model == "not_translated_yet" → fall through and process

    # branch c — cross-ID content dedup (fallback)
    for other in entries:
        if other.get("md_path") and other.get("source_content_hash") == source_hash:
            return {"status": "already_done", "md_path": other["md_path"]}

    # no manifest match, OR the source bytes changed (re-edit) → process fresh
    return PROCEED
