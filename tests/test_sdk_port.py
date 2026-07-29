"""Unit tests for the SDK subscription path's deterministic pieces.

Run: .venv/bin/python -m tests.test_sdk_port

No sessions are spawned and no network is touched — these cover the code half
of Shape D: manifest recording schemas, route-prompt rendering, the decision
schema, path construction, and the text-extraction guard.
"""

import io
import json
import tempfile
import unittest
from pathlib import Path

import pypdf

from core import manifest, vault
from sdk.prepare import FileContext
from sdk.sessions import (
    COURSE_NAMING_SCHEMA,
    ROUTE_DECISION_SCHEMA,
    _route_prompt,
    _write_instruction,
    extract_pdf_text,
)


def _ctx(**over):
    base = dict(
        file_id="F1",
        name="הרצאה 4.pdf",
        parent_path=["מבוא להתקני מוליכים למחצה", "הרצאות"],
        pdf_bytes=b"%PDF-fake",
        source_hash="sha256:abc",
        drive_md5="md5abc",
        signals={
            "recognizability": 0.9, "tokens_per_page": 250.0,
            "bytes_per_token": 40.0, "math_token_fraction": 0.05,
            "max_garbage_run_DIAGNOSTIC": 3, "page_count": 12,
            "file_size_kb": 800,
        },
        signals_full={"per_page": [1, 2], "unrecognized_sample": "xyz"},
    )
    base.update(over)
    return FileContext(**base)


class TempManifest:
    """Point core.manifest at a temp manifest seeded with []."""
    def __enter__(self):
        self._dir = tempfile.TemporaryDirectory()
        self._orig = manifest.LOG_PATH
        manifest.LOG_PATH = Path(self._dir.name) / "translated_log.json"
        manifest.LOG_PATH.write_text("[]", encoding="utf-8")
        return manifest.LOG_PATH

    def __exit__(self, *exc):
        manifest.LOG_PATH = self._orig
        self._dir.cleanup()


class RecordingTests(unittest.TestCase):
    def test_record_skip_schema(self):
        with TempManifest() as log_path:
            vault.record_skip(
                drive_file_id="F1", drive_filename="a.pdf",
                source_hash="sha256:abc", skip_reason="my solutions",
                source_md5="md5abc",
            )
            [entry] = json.loads(log_path.read_text())
        self.assertEqual(entry["model"], "skipped_permanent")
        self.assertIsNone(entry["md_path"])
        self.assertEqual(entry["skip_reason"], "my solutions")
        self.assertEqual(entry["source_md5"], "md5abc")
        self.assertEqual(entry["cost_usd"], 0)

    def test_record_skip_omits_absent_md5(self):
        with TempManifest() as log_path:
            vault.record_skip(
                drive_file_id="F1", drive_filename="a.pdf",
                source_hash="sha256:abc", skip_reason="r", source_md5=None,
            )
            [entry] = json.loads(log_path.read_text())
        self.assertNotIn("source_md5", entry)

    def test_record_translation_schema_and_upsert(self):
        with TempManifest() as log_path:
            common = dict(
                drive_file_id="F1", drive_filename="a.pdf",
                source_hash="sha256:abc", md_path_relative="C/Lectures/a_EN.md",
                course_english="C", type_value="lecture",
                cost_data={"model": "subscription:claude-opus-5", "cost_usd": 0.0,
                           "input_tokens": 10, "output_tokens": 20},
                chosen_mode="image", mode_reasoning="scanned",
                detection_signals={"recognizability": 0.5},
                source_md5="md5abc",
            )
            vault.record_translation(**common)
            # same drive_file_id again → replaced in place, not appended
            vault.record_translation(**{**common, "chosen_mode": "text"})
            entries = json.loads(log_path.read_text())
        self.assertEqual(len(entries), 1)
        e = entries[0]
        self.assertEqual(e["model"], "subscription:claude-opus-5")
        self.assertEqual(e["chosen_mode"], "text")
        self.assertEqual(e["recognizability"], 0.5)
        self.assertEqual(e["md_path"], "C/Lectures/a_EN.md")


class RoutePromptTests(unittest.TestCase):
    def test_prompt_carries_path_signals_and_mappings(self):
        p = _route_prompt(_ctx(), "- א → A", None)
        self.assertIn("מבוא להתקני מוליכים למחצה/הרצאות/הרצאה 4.pdf", p)
        self.assertIn("tokens_per_page: 250.0", p)
        self.assertIn("- א → A", p)
        self.assertNotIn("Additional context", p)

    def test_prompt_carries_reroute_feedback(self):
        p = _route_prompt(_ctx(), "(none yet)", "text mode refused: garbled")
        self.assertIn("Additional context", p)
        self.assertIn("text mode refused: garbled", p)

    def test_decision_schema_is_strict(self):
        schema = ROUTE_DECISION_SCHEMA["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertIn("skip", schema["properties"]["action"]["enum"])
        naming = COURSE_NAMING_SCHEMA["schema"]
        self.assertFalse(naming["additionalProperties"])

    def test_write_instruction_names_contract(self):
        wi = _write_instruction(Path("/v/C/Exams/x_EN.md"))
        self.assertIn("/v/C/Exams/x_EN.md", wi)
        self.assertIn("REFUSED:", wi)
        self.assertIn("SAVED", wi)


class ExtractionGuardTests(unittest.TestCase):
    def test_blank_pdf_raises(self):
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=100, height=100)
        buf = io.BytesIO()
        writer.write(buf)
        with self.assertRaises(RuntimeError):
            extract_pdf_text(buf.getvalue())


class OutputPathTests(unittest.TestCase):
    def test_other_type_uses_custom_subfolder(self):
        p = vault.vault_output_path(Path("/v"), "C", "other", "x.pdf",
                                    custom_subfolder="Reference Sheets")
        self.assertEqual(p, Path("/v/C/Reference Sheets/x_EN.md"))

    def test_unknown_type_without_custom_raises(self):
        with self.assertRaises(ValueError):
            vault.vault_output_path(Path("/v"), "C", "other", "x.pdf")


if __name__ == "__main__":
    unittest.main()
