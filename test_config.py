"""Tests for config.py — the per-user config loader.

Run standalone: `.venv/bin/python test_config.py` (uses stdlib unittest, no
pytest needed). Each test writes a config file into a temp dir and loads it via
an explicit path, so nothing here touches the real config.json.
"""
import json
import tempfile
import unittest
from pathlib import Path

import config

VALID = {
    "root_folder_id": "abc123",
    "vault_path": "/tmp/vault",
    "model": "claude-opus-4-8",
    "spend_cap_usd": 5.0,
    "tool_call_budget": 200,
}


def _write(dirpath: Path, data) -> Path:
    p = Path(dirpath) / "config.json"
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(json.dumps(data), encoding="utf-8")
    return p


class LoadConfigTests(unittest.TestCase):
    def test_valid_config_loads(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load_config(_write(d, VALID))
        self.assertIsInstance(cfg, config.Config)
        self.assertEqual(cfg.root_folder_id, "abc123")
        self.assertEqual(cfg.vault_path, "/tmp/vault")
        self.assertEqual(cfg.model, "claude-opus-4-8")
        self.assertEqual(cfg.spend_cap_usd, 5.0)
        self.assertEqual(cfg.tool_call_budget, 200)

    def test_missing_file_raises_clear_error(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / "config.json"  # never created
            with self.assertRaises(config.ConfigError) as ctx:
                config.load_config(missing)
        msg = str(ctx.exception)
        self.assertIn("config.example.json", msg)
        self.assertIn("config.json", msg)

    def test_missing_required_key_names_it(self):
        for key in ("root_folder_id", "vault_path"):
            data = {k: v for k, v in VALID.items() if k != key}
            with tempfile.TemporaryDirectory() as d:
                with self.assertRaises(config.ConfigError) as ctx:
                    config.load_config(_write(d, data))
            self.assertIn(key, str(ctx.exception))

    def test_empty_required_value_rejected(self):
        data = {**VALID, "vault_path": "   "}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError) as ctx:
                config.load_config(_write(d, data))
        self.assertIn("vault_path", str(ctx.exception))

    def test_defaults_applied_for_optional_keys(self):
        data = {"root_folder_id": "abc123", "vault_path": "/tmp/vault"}
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load_config(_write(d, data))
        self.assertEqual(cfg.model, "claude-opus-4-8")
        self.assertEqual(cfg.spend_cap_usd, 5.00)
        self.assertEqual(cfg.tool_call_budget, 200)

    def test_wrong_type_names_offending_key(self):
        data = {**VALID, "tool_call_budget": "lots"}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError) as ctx:
                config.load_config(_write(d, data))
        self.assertIn("tool_call_budget", str(ctx.exception))

    def test_bool_rejected_for_int_field(self):
        # bool is a subclass of int — make sure True isn't accepted as a budget.
        data = {**VALID, "tool_call_budget": True}
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError) as ctx:
                config.load_config(_write(d, data))
        self.assertIn("tool_call_budget", str(ctx.exception))

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(config.ConfigError):
                config.load_config(_write(d, "{not valid json"))

    def test_unknown_keys_ignored(self):
        data = {**VALID, "_note": "documentation only", "future_field": 1}
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load_config(_write(d, data))
        self.assertEqual(cfg.root_folder_id, "abc123")

    def test_int_spend_cap_coerced_to_float(self):
        data = {**VALID, "spend_cap_usd": 5}
        with tempfile.TemporaryDirectory() as d:
            cfg = config.load_config(_write(d, data))
        self.assertIsInstance(cfg.spend_cap_usd, float)
        self.assertEqual(cfg.spend_cap_usd, 5.0)

    def test_save_config_round_trips(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            cfg = config.Config(**VALID)
            config.save_config(cfg, p)
            self.assertEqual(config.load_config(p), cfg)


if __name__ == "__main__":
    unittest.main()
