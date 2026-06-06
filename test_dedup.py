"""Branch-coverage tests for dedup.py — the manifest dedup decision logic.

Pure unit tests over stubbed manifest entries: no API key, no OAuth, no Drive,
no filesystem. Run with `python3 test_dedup.py` (plain asserts, no pytest dep).

Each test pins a verdict dict to the SHAPE the original inline branches in
agent.read_file_logic returned, so a regression in dedup.py fails loudly here.
"""
import dedup

DRIVE_MD5 = "abc123md5"
SHA = "sha256:deadbeef"


# ── md5_gate (branch a, PRE-DOWNLOAD) ──────────────────────────────────────────

def test_md5_hit():
    entries = [{"drive_file_id": "F1", "md_path": "C/Lectures/x_EN.md",
                "source_md5": DRIVE_MD5}]
    assert dedup.md5_gate(entries, "F1", DRIVE_MD5) == {
        "status": "already_done", "md_path": "C/Lectures/x_EN.md"}


def test_md5_miss_different_md5():
    entries = [{"drive_file_id": "F1", "md_path": "C/x_EN.md",
                "source_md5": "OTHER"}]
    assert dedup.md5_gate(entries, "F1", DRIVE_MD5) is None


def test_md5_none_native_google_no_crash():
    # native Google Doc → Drive md5 is None → gate N/A, must NOT crash, returns None
    entries = [{"drive_file_id": "F1", "md_path": "C/x_EN.md",
                "source_md5": DRIVE_MD5}]
    assert dedup.md5_gate(entries, "F1", None) is None


def test_md5_no_mdpath_falls_through():
    # entry exists + md5 matches but never translated (no md_path) → gate must NOT fire
    entries = [{"drive_file_id": "F1", "source_md5": DRIVE_MD5,
                "model": "not_translated_yet"}]
    assert dedup.md5_gate(entries, "F1", DRIVE_MD5) is None


def test_md5_no_stored_md5_falls_through():
    entries = [{"drive_file_id": "F1", "md_path": "C/x_EN.md"}]  # no source_md5
    assert dedup.md5_gate(entries, "F1", DRIVE_MD5) is None


def test_md5_no_entry_for_id():
    entries = [{"drive_file_id": "OTHER", "md_path": "C/x_EN.md",
                "source_md5": DRIVE_MD5}]
    assert dedup.md5_gate(entries, "F1", DRIVE_MD5) is None


# ── hash_dedup branch b (by drive_file_id, hash-gated) ──────────────────────────

def test_byid_mdpath_hit():
    entries = [{"drive_file_id": "F1", "source_content_hash": SHA,
                "md_path": "C/Lectures/x_EN.md"}]
    assert dedup.hash_dedup(entries, "F1", SHA) == {
        "status": "already_done", "md_path": "C/Lectures/x_EN.md"}


def test_byid_skipped_permanent_hit_with_reason():
    entries = [{"drive_file_id": "F1", "source_content_hash": SHA,
                "model": "skipped_permanent", "skip_reason": "corrupt scan"}]
    assert dedup.hash_dedup(entries, "F1", SHA) == {
        "status": "already_done", "reason": "corrupt scan"}


def test_byid_skipped_permanent_default_reason():
    # skip_reason absent → default to the literal "skipped_permanent"
    entries = [{"drive_file_id": "F1", "source_content_hash": SHA,
                "model": "skipped_permanent"}]
    assert dedup.hash_dedup(entries, "F1", SHA) == {
        "status": "already_done", "reason": "skipped_permanent"}


def test_byid_not_translated_yet_proceeds():
    entries = [{"drive_file_id": "F1", "source_content_hash": SHA,
                "model": "not_translated_yet"}]
    v = dedup.hash_dedup(entries, "F1", SHA)
    assert v.get("status") != "already_done"
    assert v == dedup.PROCEED


def test_byid_hash_mismatch_skips_branch_b():
    # entry for this id exists and HAS md_path, but its hash differs → branch b is
    # gated off; with no cross-ID match either, must PROCEED (re-edit case).
    entries = [{"drive_file_id": "F1", "source_content_hash": "sha256:OLD",
                "md_path": "C/x_EN.md"}]
    v = dedup.hash_dedup(entries, "F1", SHA)
    assert v.get("status") != "already_done"
    assert v == dedup.PROCEED


# ── hash_dedup branch c (cross-ID SHA fallback) ─────────────────────────────────

def test_cross_id_hit():
    # no entry for F1; another id has the same content translated → already_done
    entries = [{"drive_file_id": "OTHER", "source_content_hash": SHA,
                "md_path": "C/dup_EN.md"}]
    assert dedup.hash_dedup(entries, "F1", SHA) == {
        "status": "already_done", "md_path": "C/dup_EN.md"}


def test_byid_falls_through_to_cross_id():
    # by-id entry matches hash but no md_path / not skipped (branch b falls through),
    # and ANOTHER entry has the same content with md_path → branch c catches it.
    entries = [
        {"drive_file_id": "F1", "source_content_hash": SHA,
         "model": "not_translated_yet"},
        {"drive_file_id": "OTHER", "source_content_hash": SHA,
         "md_path": "C/dup_EN.md"},
    ]
    assert dedup.hash_dedup(entries, "F1", SHA) == {
        "status": "already_done", "md_path": "C/dup_EN.md"}


def test_cross_id_requires_mdpath():
    # same content under another id but NOT translated (no md_path) → no hit → proceed
    entries = [{"drive_file_id": "OTHER", "source_content_hash": SHA,
                "model": "not_translated_yet"}]
    assert dedup.hash_dedup(entries, "F1", SHA) == dedup.PROCEED


# ── hash_dedup no-match ─────────────────────────────────────────────────────────

def test_no_match_proceeds():
    entries = [{"drive_file_id": "OTHER", "source_content_hash": "sha256:zzz",
                "md_path": "C/y_EN.md"}]
    assert dedup.hash_dedup(entries, "F1", SHA) == dedup.PROCEED


def test_empty_manifest_proceeds():
    assert dedup.hash_dedup([], "F1", SHA) == dedup.PROCEED
    assert dedup.md5_gate([], "F1", DRIVE_MD5) is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} dedup tests passed.")
