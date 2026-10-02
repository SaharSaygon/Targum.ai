"""Skip-floor is run-log only, and a rename re-offers a skipped file.

Regression for run 20260916_200723: two root-level PDFs were skip-floored
("cannot classify — no course folder") and the SDK orchestrator recorded them
as skipped_permanent, so renaming them to *_to_translate.pdf (bytes unchanged)
never brought them back. Run: .venv/bin/python -m tests.test_skip_floor_and_rename
"""
import asyncio
import json
import unittest

from core import dedup, prepass
from sdk import agent_sdk, sessions
from sdk.sessions import ROUTE_DECISION_SCHEMA
from tests.test_sdk_port import TempManifest, _ctx

SKIPPED = {
    "drive_file_id": "f1",
    "drive_file_name": "updated_courses.pdf",
    "source_content_hash": "sha256:aaa",
    "md_path": None,
    "model": "skipped_permanent",
    "skip_reason": "cannot classify",
    "source_md5": "md5-1",
}
TRANSLATED = {
    "drive_file_id": "f2",
    "drive_file_name": "plan.pdf",
    "source_content_hash": "sha256:bbb",
    "md_path": "General/Reference/plan_EN.md",
    "source_md5": "md5-2",
}
LEGACY_SKIP = {  # pre-dates stored names
    "drive_file_id": "f3",
    "md_path": None,
    "model": "skipped_permanent",
    "source_md5": "md5-3",
}


class RenameReoffersSkippedFile(unittest.TestCase):
    def test_same_name_stays_dropped(self):
        self.assertTrue(dedup.skip_unchanged(
            [SKIPPED], "f1", "md5-1", drive_name="updated_courses.pdf"))

    def test_renamed_skip_is_reoffered(self):
        self.assertFalse(dedup.skip_unchanged(
            [SKIPPED], "f1", "md5-1", drive_name="updated_courses_to_translate.pdf"))

    def test_name_blind_without_drive_name(self):
        self.assertTrue(dedup.skip_unchanged([SKIPPED], "f1", "md5-1"))

    def test_entry_without_stored_name_is_name_blind(self):
        self.assertTrue(dedup.skip_unchanged(
            [LEGACY_SKIP], "f3", "md5-3", drive_name="whatever.pdf"))

    def test_hash_dedup_lets_renamed_skip_through(self):
        same = dedup.hash_dedup([SKIPPED], "f1", "sha256:aaa",
                                drive_name="updated_courses.pdf")
        self.assertEqual(same["status"], "already_done")
        renamed = dedup.hash_dedup([SKIPPED], "f1", "sha256:aaa",
                                   drive_name="updated_courses_to_translate.pdf")
        self.assertEqual(renamed, dedup.PROCEED)

    def test_renamed_translated_file_stays_done(self):
        self.assertIsNotNone(dedup.md5_gate([TRANSLATED], "f2", "md5-2"))
        v = dedup.hash_dedup([TRANSLATED], "f2", "sha256:bbb", drive_name="new.pdf")
        self.assertEqual(v["status"], "already_done")

    def test_modified_gate_reoffers_renamed_native_skip(self):
        mt = "2026-06-19T14:37:41.216Z"
        e = {**SKIPPED, "source_modified_time": mt}
        self.assertTrue(dedup.modified_unchanged([e], "f1", mt, drive_name="updated_courses.pdf"))
        self.assertFalse(dedup.modified_unchanged([e], "f1", mt, drive_name="x_to_translate.pdf"))
        t = {**TRANSLATED, "source_modified_time": mt}
        self.assertTrue(dedup.modified_unchanged([t], "f2", mt, drive_name="renamed.pdf"))

    def test_diff_tree_worklists_renamed_skip_only(self):
        files = [
            {"id": "f1", "name": "updated_courses_to_translate.pdf",
             "parent_path": [], "md5": "md5-1", "modified_time": None},
            {"id": "f2", "name": "plan renamed.pdf",
             "parent_path": [], "md5": "md5-2", "modified_time": None},
        ]
        wl = prepass.diff_tree(files, [SKIPPED, TRANSLATED])
        self.assertEqual([w["file_id"] for w in wl], ["f1"])
        files[0]["name"] = "updated_courses.pdf"
        self.assertEqual(prepass.diff_tree(files, [SKIPPED, TRANSLATED]), [])


class SkipFloorIsRunLogOnly(unittest.TestCase):
    def test_schema_carries_permanent_flag(self):
        props = ROUTE_DECISION_SCHEMA["schema"]["properties"]
        self.assertEqual(props["permanent"]["type"], "boolean")
        self.assertIn("permanent", ROUTE_DECISION_SCHEMA["schema"]["required"])

    def test_prompt_explains_permanent(self):
        p = sessions._route_prompt(_ctx(), "(none yet)", None)
        self.assertIn("permanent=false", p)

    def _run(self, decision):
        ctx = _ctx()
        summary = agent_sdk._empty_summary()
        result = type("R", (), {"usage": {}, "duration_ms": 1})()

        async def fake_route(ctx, courses_block, model, extra_context=None):
            return decision, result

        saved = (agent_sdk.prepare_file, sessions.route_file,
                 agent_sdk._record_session, agent_sdk.LOG, agent_sdk.CONFIG)
        agent_sdk.prepare_file = lambda item: {"status": "ready", "ctx": ctx}
        sessions.route_file = fake_route
        agent_sdk._record_session = lambda *a, **k: None
        agent_sdk.LOG = lambda msg: None
        agent_sdk.CONFIG = type("C", (), {"model": "m"})()
        try:
            with TempManifest() as log_path:
                asyncio.run(agent_sdk._process_file_once(
                    {"file_id": ctx.file_id, "name": ctx.name,
                     "parent_path": ctx.parent_path}, ctx.path, summary))
                entries = json.loads(log_path.read_text())
        finally:
            (agent_sdk.prepare_file, sessions.route_file, agent_sdk._record_session,
             agent_sdk.LOG, agent_sdk.CONFIG) = saved
        return entries, summary

    def test_skip_floor_writes_no_manifest_entry(self):
        entries, summary = self._run({
            "action": "skip", "skip_reason": "cannot classify", "permanent": False,
            "mode": None, "course_english": None, "file_type": None,
            "custom_subfolder": None, "reasoning": "r"})
        self.assertEqual(entries, [])
        self.assertEqual(summary["skipped"], [])
        self.assertEqual(summary["unprocessed"], [("הרצאה 4.pdf", "cannot classify")])

    def test_deliberate_skip_is_recorded(self):
        entries, summary = self._run({
            "action": "skip", "skip_reason": "own solution", "permanent": True,
            "mode": None, "course_english": None, "file_type": None,
            "custom_subfolder": None, "reasoning": "r"})
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["model"], "skipped_permanent")
        self.assertEqual(summary["skipped"], [("הרצאה 4.pdf", "own solution")])
        self.assertEqual(summary["unprocessed"], [])


if __name__ == "__main__":
    unittest.main()
