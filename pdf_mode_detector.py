"""
Heuristic classifier: should this PDF be processed as extracted text or as images?

Biases hard toward "image" — text-mode on handwritten content silently fabricates;
image-mode on typed content just costs more tokens.
"""

import io
import re

MATH_SYMBOLS: frozenset[str] = frozenset("=+-*/^<>≤≥≠±∞∑∫∂√παβγθω")

_HEBREW_RE = re.compile(r"[֐-׿]")
_MATH_ITALIC_CHAR_RE = re.compile(r"^[\U0001D400-\U0001D7FF]+$")
_LATIN_WORD_RE = re.compile(r"^[A-Za-z]{2,}$")
_NUMBER_RE = re.compile(r"^[0-9]+([.,][0-9]+)?$")


def _is_recognizable(token: str) -> bool:
    if _HEBREW_RE.search(token):
        return True
    if _LATIN_WORD_RE.match(token):
        return True
    if _NUMBER_RE.match(token):
        return True
    if len(token) == 1 and token in MATH_SYMBOLS:
        return True
    if _MATH_ITALIC_CHAR_RE.match(token):
        return True
    return False


def _analyze_text(text: str) -> dict:
    """Core logic, exposed separately so unit tests can inject text directly."""
    if len(text) < 50:
        return {
            "mode": "image",
            "recognizability": 0.0,
            "max_garbage_run": 0,
            "total_tokens": 0,
            "reason": f"image: only {len(text)} chars extracted, below 50-char threshold",
        }

    tokens = text.split()
    total = len(tokens)

    if total == 0:
        return {
            "mode": "image",
            "recognizability": 0.0,
            "max_garbage_run": 0,
            "total_tokens": 0,
            "reason": "image: no tokens after splitting",
        }

    flags = [_is_recognizable(t) for t in tokens]
    recognized = sum(flags)
    recognizability = recognized / total

    max_garbage_run = 0
    current_run = 0
    for flag in flags:
        if not flag:
            current_run += 1
            if current_run > max_garbage_run:
                max_garbage_run = current_run
        else:
            current_run = 0

    if recognizability < 0.85:
        mode = "image"
        reason = f"image: {recognizability:.0%} recognizable, below 85% threshold"
    elif max_garbage_run > 20:
        mode = "image"
        reason = f"image: {max_garbage_run}-token garbage run detected"
    else:
        mode = "text"
        reason = (
            f"text: {recognizability:.0%} recognizable, "
            f"max garbage run {max_garbage_run}"
        )

    return {
        "mode": mode,
        "recognizability": round(recognizability, 4),
        "max_garbage_run": max_garbage_run,
        "total_tokens": total,
        "reason": reason,
    }


def detect_pdf_mode(pdf_bytes: bytes) -> dict:
    """
    Classify a PDF as suitable for text extraction or image-based OCR.

    Returns a dict:
        mode            "text" | "image"
        recognizability float  fraction of whitespace-split tokens that look real
        max_garbage_run int    longest consecutive run of unrecognizable tokens
        total_tokens    int
        reason          str    human-readable explanation of the decision
    """
    try:
        import pypdf  # noqa: PLC0415

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        text = "".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        return {
            "mode": "image",
            "recognizability": 0.0,
            "max_garbage_run": 0,
            "total_tokens": 0,
            "reason": f"image: pypdf extraction failed ({exc})",
        }

    return _analyze_text(text)


# ---------------------------------------------------------------------------
# Unit tests  (python pdf_mode_detector.py)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _passed = 0
    _failed = 0

    def _check(name: str, text: str, expected_mode: str) -> None:
        global _passed, _failed
        result = _analyze_text(text)
        ok = result["mode"] == expected_mode
        status = "PASS" if ok else "FAIL"
        print(f"[{status}] {name}")
        print(f"       {result['reason']}")
        print(
            f"       tokens={result['total_tokens']}  "
            f"recognizability={result['recognizability']:.4f}  "
            f"max_garbage_run={result['max_garbage_run']}"
        )
        if not ok:
            print(f"       expected={expected_mode!r}, got={result['mode']!r}")
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
    # Test 1: empty string → image
    # ------------------------------------------------------------------
    _check(
        "empty string",
        "",
        "image",
    )

    # ------------------------------------------------------------------
    # Test 2: 30 chars → image  (below 50-char threshold)
    # ------------------------------------------------------------------
    _check(
        "30 chars total",
        "hello world foo bar baz",   # 23 chars, well under 50
        "image",
    )

    # ------------------------------------------------------------------
    # Test 3: clean Hebrew paragraph, 100% recognizable → text
    # ------------------------------------------------------------------
    _check(
        "clean Hebrew paragraph (100% recognizable, no garbage runs)",
        " ".join(_heb(60)),          # ~240 chars, 60 tokens, all Hebrew
        "text",
    )

    # ------------------------------------------------------------------
    # Test 4: 90% recognizable BUT 30-token garbage run → image (L4 case)
    # Handwritten body hidden under a typed scaffold: high overall
    # recognizability would pass the 85% check, but the long garbage run
    # betrays the handwritten section.
    # ------------------------------------------------------------------
    _tokens4 = _heb(150) + _garbage(30) + _heb(120)  # 300 tokens, 270 good
    _check(
        "90% recognizable + 30-token garbage run (L4 case)",
        " ".join(_tokens4),
        "image",
    )

    # ------------------------------------------------------------------
    # Test 5: 70% recognizable, no long garbage run → image (below threshold)
    # Garbage is evenly spread (max run = 3) so the garbage-run gate
    # doesn't fire, but recognizability < 85% catches it.
    # ------------------------------------------------------------------
    _segment5 = _heb(7) + _garbage(3)   # 10 tokens, max_run = 3
    _tokens5 = _segment5 * 10           # 100 tokens: 70 good, 30 garbage
    _check(
        "70% recognizable, no long garbage run",
        " ".join(_tokens5),
        "image",
    )

    # ------------------------------------------------------------------
    # Test 6: mixed Hebrew + LaTeX-style tokens, 95% recognizable → text
    # ------------------------------------------------------------------
    _tokens6 = (
        _heb(60)                                          # 60 Hebrew words
        + ["hello", "world", "text", "note", "case"] * 5  # 25 Latin words
        + ["=", "+", "α", "β", "γ", "42", "3.14"] * 2    # 14 math/numbers
        + _garbage(5)                                      # 5 garbage (at end, run=5)
    )
    # Total = 104 tokens, 99 recognizable → 95.2%; max_garbage_run = 5
    _check(
        "mixed Hebrew + LaTeX, 95% recognizable",
        " ".join(_tokens6),
        "text",
    )

    # ------------------------------------------------------------------
    # Token-level tests for the math-italic clause
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
