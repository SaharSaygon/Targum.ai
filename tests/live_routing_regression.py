"""LIVE routing-judgment regression for the SDK route session (Shape D).

Spawns 4 real route sessions (subscription-billed, ~small) against synthetic
FileContexts reproducing decided cases from the July 2026 manifest history, and
checks the structured decisions match what the legacy agent decided:

  1. handwritten פתר exam solution, no לתרגם     → SKIP   (ownership/handwritten)
  2. same signals under a פתור-לתרגם segment      → TRANSLATE, image (override)
  3. typed solution under פתרונות שלי             → SKIP   (ownership marker)
  4. low tokens_per_page scanned lecture          → TRANSLATE, image (mode rubric)

Run deliberately: .venv/bin/python -m tests.live_routing_regression
NOT part of the unit suite (network + model calls).
"""

import asyncio
import json

from sdk.prepare import FileContext
from sdk.sessions import route_file

COURSES_BLOCK = (
    "- מבוא להתקני מוליכים למחצה → Introduction to Semiconductor Devices\n"
    "- מבוא למערכות לינאריות → Introduction to Linear Systems"
)

HANDWRITTEN = {  # from agent_20260724 log: tokens_per_page 31, recog 0.55
    "recognizability": 0.55, "tokens_per_page": 31.0, "bytes_per_token": 48147.0,
    "math_token_fraction": 0.02, "max_garbage_run_DIAGNOSTIC": 14,
    "page_count": 6, "file_size_kb": 2400,
}
SCANNED_LECTURE = {  # from עזרתון case: tpp 49.7, recog 0.54
    "recognizability": 0.54, "tokens_per_page": 49.7, "bytes_per_token": 6962.0,
    "math_token_fraction": 0.06, "max_garbage_run_DIAGNOSTIC": 17,
    "page_count": 41, "file_size_kb": 13856,
}
TYPED = {
    "recognizability": 0.92, "tokens_per_page": 260.0, "bytes_per_token": 38.0,
    "math_token_fraction": 0.08, "max_garbage_run_DIAGNOSTIC": 3,
    "page_count": 8, "file_size_kb": 400,
}

CASES = [
    (
        "handwritten own exam solution → SKIP",
        FileContext("T1", "פתרון מועד א 2022.pdf",
                    ["מבוא להתקני מוליכים למחצה", "מבחנים"],
                    b"", "sha256:t1", "m1", dict(HANDWRITTEN), {}),
        lambda d: d["action"] == "skip",
    ),
    (
        "לתרגם override → TRANSLATE image",
        FileContext("T2", "פתרון מועד א 2022.pdf",
                    ["מבוא להתקני מוליכים למחצה", "מבחנים", "פתור-לתרגם"],
                    b"", "sha256:t2", "m2", dict(HANDWRITTEN), {}),
        lambda d: d["action"] == "translate" and d["mode"] == "image",
    ),
    (
        "typed solution under פתרונות שלי → SKIP",
        FileContext("T3", "מלמ 21א פתור.pdf",
                    ["מבוא להתקני מוליכים למחצה", "מבחנים", "פתרונות שלי"],
                    b"", "sha256:t3", "m3", dict(TYPED), {}),
        lambda d: d["action"] == "skip",
    ),
    (
        "low tokens_per_page scanned tutorial → TRANSLATE image",
        FileContext("T4", "עזרתון מל״מ מפגש 7 מלא.pdf",
                    ["מבוא להתקני מוליכים למחצה", "עזרתון"],
                    b"", "sha256:t4", "m4", dict(SCANNED_LECTURE), {}),
        lambda d: d["action"] == "translate" and d["mode"] == "image",
    ),
]


async def main():
    passed = 0
    for name, ctx, check in CASES:
        decision, result = await route_file(ctx, COURSES_BLOCK, "claude-opus-5")
        ok = check(decision)
        passed += ok
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
        print(f"      decision: {json.dumps(decision, ensure_ascii=False)}")
        u = result.usage or {}
        print(f"      tokens in/cr/out: {u.get('input_tokens')}/"
              f"{u.get('cache_read_input_tokens')}/{u.get('output_tokens')}")
    print(f"\n{passed}/{len(CASES)} routing-regression cases passed")
    return passed == len(CASES)


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(main()) else 1)
