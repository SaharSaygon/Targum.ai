"""Tests for the native-Google-Doc freshness gate (dedup.modified_unchanged +
its wiring in prepass.diff_tree).

Native Docs/Sheets/Slides have no md5Checksum, so the md5 gates can never drop
them — before this gate they reappeared on the worklist every run. Run
standalone: `.venv/bin/python -m tests.test_native_doc_gate`.
"""
import unittest

from core import dedup, prepass

MT = "2026-06-19T14:37:41.216Z"

TRANSLATED = {
    "drive_file_id": "doc1",
    "md_path": "Course/Reference/doc1_EN.md",
    "source_content_hash": "sha256:aaa",
    "source_modified_time": MT,
}
SKIPPED = {
    "drive_file_id": "doc2",
    "md_path": None,
    "model": "skipped_permanent",
    "source_modified_time": MT,
}
NO_MT = {
    "drive_file_id": "doc3",
    "md_path": "Course/Reference/doc3_EN.md",
}


class ModifiedUnchangedTests(unittest.TestCase):
    def test_translated_doc_unchanged(self):
        self.assertTrue(dedup.modified_unchanged([TRANSLATED], "doc1", MT))

    def test_skipped_doc_unchanged(self):
        self.assertTrue(dedup.modified_unchanged([SKIPPED], "doc2", MT))

    def test_modified_doc_falls_through(self):
        self.assertFalse(dedup.modified_unchanged(
            [TRANSLATED], "doc1", "2026-07-01T00:00:00.000Z"))

    def test_entry_without_stored_time_falls_through(self):
        # pre-backfill entries have no source_modified_time → must reprocess
        self.assertFalse(dedup.modified_unchanged([NO_MT], "doc3", MT))

    def test_unknown_id_falls_through(self):
        self.assertFalse(dedup.modified_unchanged([TRANSLATED], "nope", MT))

    def test_none_modified_time_falls_through(self):
        self.assertFalse(dedup.modified_unchanged([TRANSLATED], "doc1", None))


class DiffTreeNativeDocTests(unittest.TestCase):
    def _file(self, fid, md5, mt):
        return {"id": fid, "name": fid, "parent_path": ["Course"],
                "md5": md5, "modified_time": mt}

    def test_unchanged_native_doc_dropped(self):
        files = [self._file("doc1", None, MT)]
        self.assertEqual(prepass.diff_tree(files, [TRANSLATED]), [])

    def test_touched_native_doc_worklisted(self):
        files = [self._file("doc1", None, "2026-07-01T00:00:00.000Z")]
        wl = prepass.diff_tree(files, [TRANSLATED])
        self.assertEqual([w["file_id"] for w in wl], ["doc1"])

    def test_binary_file_never_uses_modified_gate(self):
        # md5 present but mismatched → worklisted even if modifiedTime matches
        entry = {**TRANSLATED, "source_md5": "old-md5"}
        files = [self._file("doc1", "new-md5", MT)]
        wl = prepass.diff_tree(files, [entry])
        self.assertEqual([w["file_id"] for w in wl], ["doc1"])


if __name__ == "__main__":
    unittest.main()
