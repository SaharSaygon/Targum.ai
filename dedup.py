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
from manifest import find_by_id

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


def hash_dedup(entries, drive_file_id, source_hash):
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

    No hit in either branch → PROCEED (caller runs the detector and translates)."""
    # branch b — by drive_file_id, gated on hash match
    entry = find_by_id(entries, drive_file_id)
    if entry is not None and entry.get("source_content_hash") == source_hash:
        if entry.get("md_path"):
            # already translated (manual or by a prior run)
            return {"status": "already_done", "md_path": entry["md_path"]}
        if entry.get("model") == "skipped_permanent":
            return {"status": "already_done",
                    "reason": entry.get("skip_reason", "skipped_permanent")}
        # model == "not_translated_yet" → fall through and process

    # branch c — cross-ID content dedup (fallback)
    for other in entries:
        if other.get("md_path") and other.get("source_content_hash") == source_hash:
            return {"status": "already_done", "md_path": other["md_path"]}

    # no manifest match, OR the source bytes changed (re-edit) → process fresh
    return PROCEED
