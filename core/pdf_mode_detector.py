"""
Extraction-signal reporter for PDFs: how well did pypdf read this file, and what
does the unread remainder look like?

This module NO LONGER returns a text/image verdict. It reports raw signals and
the routing agent owns the decision (see agent_routing_prompt.md "Mode
Selection"). The bias toward image on ambiguity lives in the agent's rubric, not
here — keeping the detector a pure, side-effect-free measurement function makes
its output auditable and the decision logic inspectable in one place.
"""

import io
import re

MATH_SYMBOLS: frozenset[str] = frozenset("=+-*/^<>≤≥≠±∞∑∫∂√παβγθω")

_HEBREW_RE = re.compile(r"[֐-׿]")
_MATH_ITALIC_CHAR_RE = re.compile(r"^[\U0001D400-\U0001D7FF]+$")
_LATIN_WORD_RE = re.compile(r"^[A-Za-z]{2,}$")
_NUMBER_RE = re.compile(r"^[0-9]+([.,][0-9]+)?$")

# ---------------------------------------------------------------------------
# Heuristic thresholds — RETAINED FOR REFERENCE / MONITORING ONLY.
# These were the old verdict cutoffs. They are intentionally NOT applied
# anywhere below: the detector emits raw signals and the routing agent decides.
# Kept so historical reasoning and threshold-retuning analysis stay grounded.
# ---------------------------------------------------------------------------
_RECOGNIZABILITY_THRESHOLD = 0.85  # old text/image recognizability cutoff
_MAX_GARBAGE_RUN_THRESHOLD = 20    # old garbage-run cutoff
_MIN_TEXT_CHARS = 50               # old minimum-extracted-text cutoff


def _is_math_token(token: str) -> bool:
    """Math-token test.

    Factored out of _is_recognizable so math_token_fraction reuses the SAME
    logic instead of a second, divergent classifier. Covers lone math
    operators/symbols and pure Mathematical-Italic (U+1D400–U+1D7FF) runs.

    NOTE: fused digit-and-operator clusters (e.g. "𝑎2+𝜔2", "ℱ{𝑓(𝑡−𝑡0)}=…") are
    deliberately NOT counted here — counting them would also flip them to
    "recognizable" and break recognizability parity. They instead fall through
    to unrecognized_sample, which is exactly the "fragmented math" signal the
    routing rubric reads.
    """
    if len(token) == 1 and token in MATH_SYMBOLS:
        return True
    if _MATH_ITALIC_CHAR_RE.match(token):
        return True
    return False


def _is_recognizable(token: str) -> bool:
    if _HEBREW_RE.search(token):
        return True
    if _LATIN_WORD_RE.match(token):
        return True
    if _NUMBER_RE.match(token):
        return True
    if _is_math_token(token):
        return True
    return False


def _analyze_text(text: str) -> dict:
    """Doc-level extraction signals for a block of text.

    Exposed separately so unit tests can inject text directly. Returns raw
    signals only — no mode verdict. All the original math is preserved
    (recognizability, garbage-run); we changed what's exposed, not how it's
    computed.
    """
    tokens = text.split()
    total = len(tokens)

    if total == 0:
        return {
            "recognizability": 0.0,
            "math_token_fraction": 0.0,
            "unrecognized_sample": "",
            "max_garbage_run_DIAGNOSTIC": 0,
            "total_tokens": 0,
        }

    flags = [_is_recognizable(t) for t in tokens]
    recognized = sum(flags)
    recognizability = recognized / total

    math_count = sum(_is_math_token(t) for t in tokens)
    math_token_fraction = math_count / total

    # Verbatim slice of the tokens that failed recognition. The garbage SHAPE is
    # the signal, so this is NOT cleaned, normalized, or encoding-fixed.
    unrecognized = [t for t, ok in zip(tokens, flags) if not ok]
    unrecognized_sample = " ".join(unrecognized)[:300]

    # Longest consecutive run of unrecognizable tokens.
    max_garbage_run = 0
    current_run = 0
    for flag in flags:
        if not flag:
            current_run += 1
            if current_run > max_garbage_run:
                max_garbage_run = current_run
        else:
            current_run = 0

    return {
        "recognizability": round(recognizability, 4),
        "math_token_fraction": round(math_token_fraction, 4),
        "unrecognized_sample": unrecognized_sample,
        # RTL/math extraction-quality signal, NOT a handwriting detector — in our
        # corpus fires only on typed RTL+math files. Do not route on this alone.
        "max_garbage_run_DIAGNOSTIC": max_garbage_run,
        "total_tokens": total,
    }


def detect_pdf_mode(pdf_bytes: bytes) -> dict:
    """
    Report raw extraction signals for a PDF. NO verdict — the routing agent
    weighs these (see agent_routing_prompt.md "Mode Selection").

    Returns a dict:
        DECISION SIGNALS
        recognizability       float 0–1   doc-level fraction of tokens that parse
                                           as real Hebrew/Latin/math
        tokens_per_page       float       extraction yield (total tokens / pages)
        bytes_per_token       float       file bytes / token; high → raster/scanned
        math_token_fraction   float 0–1   fraction of tokens that are math
        unrecognized_sample   str         ~300-char verbatim slice of failed tokens
        per_page              list[dict]  {page, tokens, recognizability, math_fraction}

        DIAGNOSTIC-ONLY (not verdict drivers)
        max_garbage_run_DIAGNOSTIC  int   longest unrecognizable run
        page_count            int
        file_size_kb          int
    """
    file_size_kb = len(pdf_bytes) // 1024

    try:
        import pypdf  # noqa: PLC0415

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        page_texts = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        # Extraction failed → zero yield. Reported as such (very low tokens, very
        # high bytes/token) so the agent routes to image. No verdict emitted.
        return {
            "recognizability": 0.0,
            "tokens_per_page": 0.0,
            "bytes_per_token": float(file_size_kb * 1024),
            "math_token_fraction": 0.0,
            "unrecognized_sample": "",
            "per_page": [],
            # RTL/math extraction-quality signal, NOT a handwriting detector — in
            # our corpus fires only on typed RTL+math files. Do not route on this
            # alone.
            "max_garbage_run_DIAGNOSTIC": 0,
            "page_count": 0,
            "file_size_kb": file_size_kb,
        }

    page_count = len(page_texts)

    # Doc-level signals use the same concatenation the detector always used.
    doc = _analyze_text("".join(page_texts))
    total_tokens = doc["total_tokens"]

    per_page = []
    for i, ptext in enumerate(page_texts, start=1):
        pa = _analyze_text(ptext)
        per_page.append({
            "page": i,
            "tokens": pa["total_tokens"],
            "recognizability": pa["recognizability"],
            "math_fraction": pa["math_token_fraction"],
        })

    tokens_per_page = total_tokens / max(page_count, 1)
    bytes_per_token = (file_size_kb * 1024) / max(total_tokens, 1)

    return {
        "recognizability": doc["recognizability"],
        "tokens_per_page": round(tokens_per_page, 2),
        "bytes_per_token": round(bytes_per_token, 2),
        "math_token_fraction": doc["math_token_fraction"],
        "unrecognized_sample": doc["unrecognized_sample"],
        "per_page": per_page,
        # RTL/math extraction-quality signal, NOT a handwriting detector — in our
        # corpus fires only on typed RTL+math files. Do not route on this alone.
        "max_garbage_run_DIAGNOSTIC": doc["max_garbage_run_DIAGNOSTIC"],
        "page_count": page_count,
        "file_size_kb": file_size_kb,
    }


# ---------------------------------------------------------------------------
# Unit tests  (python pdf_mode_detector.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _passed = 0
    _failed = 0

    def _check(
        name: str,
        text: str,
        exp_recognizability: float,
        exp_garbage_run: int,
    ) -> None:
        """Assert on the raw signals (recognizability + garbage-run) now that the
        detector no longer emits a 'mode' verdict."""
        global _passed, _failed
        result = _analyze_text(text)
        recog_ok = abs(result["recognizability"] - exp_recognizability) < 0.005
        run_ok = result["max_garbage_run_DIAGNOSTIC"] == exp_garbage_run
        ok = recog_ok and run_ok
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}")
        print(
            f"       tokens={result['total_tokens']}  "
            f"recognizability={result['recognizability']:.4f}  "
            f"math_fraction={result['math_token_fraction']:.4f}  "
            f"max_garbage_run_DIAGNOSTIC={result['max_garbage_run_DIAGNOSTIC']}"
        )
        if not ok:
            print(
                f"       expected recognizability≈{exp_recognizability}, "
                f"garbage_run={exp_garbage_run}"
            )
        print()
        if ok:
            _passed += 1
        else:
            _failed += 1

    # Token pools
    _HEB = [
        "שלום", "עולם", "זה", "הוא", "היא", "אנחנו", "כן", "לא",
        "טוב", "רע", "מה", "מי", "למה", "כי", "גם", "אם", "אבל",
    ]
    # Garbage: non-Hebrew, non-Latin, non-number, non-math-symbol multi-char tokens
    _GARBAGE = ["♠♠♠", "◊◊◊", "■■■", "▲▲▲", "●●●", "∎∎∎", "⊗⊗⊗"]

    def _heb(n: int) -> list[str]:
        return [_HEB[i % len(_HEB)] for i in range(n)]

    def _garbage(n: int) -> list[str]:
        return [_GARBAGE[i % len(_GARBAGE)] for i in range(n)]

    # ------------------------------------------------------------------
    # Test 1: empty string → zero tokens, zero recognizability
    # (Was: "→ image". Mode verdict removed; assert raw signals instead.)
    # ------------------------------------------------------------------
    _check(
        "empty string",
        "",
        exp_recognizability=0.0,
        exp_garbage_run=0,
    )

    # ------------------------------------------------------------------
    # Test 2: short all-Latin text → 100% recognizable, no garbage run
    # (Was: "30 chars → image" via the old <50-char short-circuit. That
    # verdict short-circuit is gone — low yield is now a routing concern via
    # tokens_per_page — so we assert the underlying recognizability instead.)
    # ------------------------------------------------------------------
    _check(
        "short all-Latin text (5 tokens, all recognizable)",
        "hello world foo bar baz",   # 5 Latin tokens
        exp_recognizability=1.0,
        exp_garbage_run=0,
    )

    # ------------------------------------------------------------------
    # Test 3: clean Hebrew paragraph, 100% recognizable
    # (Was: "→ text".)
    # ------------------------------------------------------------------
    _check(
        "clean Hebrew paragraph (100% recognizable, no garbage runs)",
        " ".join(_heb(60)),          # ~240 chars, 60 tokens, all Hebrew
        exp_recognizability=1.0,
        exp_garbage_run=0,
    )

    # ------------------------------------------------------------------
    # Test 4: 90% recognizable with a 30-token garbage run (L4 case)
    # (Was: "→ image".) Assert the 90% recognizability and the 30-run that the
    # old verdict keyed on — now reported as max_garbage_run_DIAGNOSTIC.
    # ------------------------------------------------------------------
    _tokens4 = _heb(150) + _garbage(30) + _heb(120)  # 300 tokens, 270 good
    _check(
        "90% recognizable + 30-token garbage run (L4 case)",
        " ".join(_tokens4),
        exp_recognizability=0.9,
        exp_garbage_run=30,
    )

    # ------------------------------------------------------------------
    # Test 5: 70% recognizable, garbage evenly spread (max run = 3)
    # (Was: "→ image".)
    # ------------------------------------------------------------------
    _segment5 = _heb(7) + _garbage(3)   # 10 tokens, max_run = 3
    _tokens5 = _segment5 * 10           # 100 tokens: 70 good, 30 garbage
    _check(
        "70% recognizable, no long garbage run",
        " ".join(_tokens5),
        exp_recognizability=0.7,
        exp_garbage_run=3,
    )

    # ------------------------------------------------------------------
    # Test 6: mixed Hebrew + LaTeX-style tokens, ~95% recognizable
    # (Was: "→ text".) 104 tokens, 99 recognizable → 0.9519; garbage run 5.
    # ------------------------------------------------------------------
    _tokens6 = (
        _heb(60)                                          # 60 Hebrew words
        + ["hello", "world", "text", "note", "case"] * 5  # 25 Latin words
        + ["=", "+", "α", "β", "γ", "42", "3.14"] * 2    # 14 math/numbers
        + _garbage(5)                                      # 5 garbage (at end, run=5)
    )
    _check(
        "mixed Hebrew + LaTeX, 95% recognizable",
        " ".join(_tokens6),
        exp_recognizability=0.9519,
        exp_garbage_run=5,
    )

    # ------------------------------------------------------------------
    # Token-level tests for the math-italic clause (unchanged — these exercise
    # _is_recognizable, whose behavior is preserved by the _is_math_token
    # factoring).
    # ------------------------------------------------------------------
    print("--- token-level recognizability tests ---\n")

    def _check_tok(token: str, expected: bool) -> None:
        global _passed, _failed
        got = _is_recognizable(token)
        ok = got == expected
        status = "PASS" if ok else "FAIL"
        mark = "✓" if expected else "✗"
        print(f"[{status}] {token!r:40s} len={len(token)}  expected={mark}  got={'✓' if got else '✗'}")
        if ok:
            _passed += 1
        else:
            _failed += 1

    # pure math-italic (every char in U+1D400–U+1D7FF) → recognizable
    _check_tok("𝑆",                               True)   # single char
    _check_tok("𝑗𝜔",                              True)   # multi-char, all math-italic
    _check_tok("𝑎𝑏𝑐𝑑𝑒",                           True)   # pure math-italic, any length
    # mixed (parens, digits, ASCII, operators) → NOT recognizable
    _check_tok("dist(𝑢)",                         False)  # ASCII letters + parens disqualify
    _check_tok("𝑓(𝑡)",                            False)  # parens disqualify
    _check_tok("𝑎→0+",                            False)  # arrow + digit + plus disqualify
    _check_tok("𝑎2+𝜔2",                           False)  # digits + plus disqualify
    _check_tok("lim𝑡→±∞",                         False)  # ASCII letters + arrow disqualify
    _check_tok("ℱ{𝑓(𝑡−𝑡0)}=𝑒−𝑗𝜔𝑡0𝐹(𝜔)",          False)  # long fused formula, L4 garbage

    print()
    # ------------------------------------------------------------------
    print(f"{_passed}/{_passed + _failed} tests passed")
