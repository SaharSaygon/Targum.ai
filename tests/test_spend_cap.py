"""Tests for the API-path spend cap (legacy/agent.py).

Run standalone: `.venv/bin/python -m tests.test_spend_cap` (stdlib unittest).
Pure-function tests over stubbed ledger rows — no API calls, no real config
values matter (legacy.agent loads config.json at import, which exists on the
dev machine).
"""
import unittest

from legacy.agent import spend_cap_reached


def _rows(*costs):
    return [{"cost_usd": c} for c in costs]


class SpendCapTests(unittest.TestCase):
    def test_empty_ledger_never_hits_cap(self):
        self.assertFalse(spend_cap_reached([], 0.01))

    def test_under_cap(self):
        self.assertFalse(spend_cap_reached(_rows(0.002, 0.003), 0.01))

    def test_exactly_at_cap_stops(self):
        self.assertTrue(spend_cap_reached(_rows(0.005, 0.005), 0.01))

    def test_over_cap_stops(self):
        self.assertTrue(spend_cap_reached(_rows(0.02, 1.5), 0.01))

    def test_tiny_cap_with_mixed_categories(self):
        # cap is checked against ALL rows (routing + translation alike)
        rows = _rows(0.0004, 0.0004, 0.0004)
        self.assertTrue(spend_cap_reached(rows, 0.001))
        self.assertFalse(spend_cap_reached(rows[:2], 0.001))


if __name__ == "__main__":
    unittest.main()
