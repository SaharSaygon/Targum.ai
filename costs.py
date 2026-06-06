"""Cache-aware per-call cost + token ledger. Stdlib only — no anthropic, no
drive, no client; importable bare (no API key, no OAuth).

One recorder, record_call(), runs after every messages.create at the two LLM
call sites (routing in agent.py; translation in translation_engine via an
on_usage callback). It computes a cache-TIERED cost, appends an OTel-aligned
JSON line to the run ledger, and returns the row so the caller can roll it up.
"""
import json
from datetime import datetime, timezone

# Claude Opus 4.8 pricing — USD per 1e6 tokens. The four token classes are
# DISJOINT in the Anthropic usage object (input_tokens already EXCLUDES the
# cached ones), so each is multiplied by its own rate and the products are summed
# — never subtract one class from another.
#   input        fresh / uncached input             $5.00
#   output       generated output                   $25.00
#   cache_write  5-minute ephemeral cache WRITE      $6.25   (1.25× input)
#   cache_read   cache READ / hit                    $0.50   (0.1×  input)
# CAVEAT: a ttl:"1h" cache WRITE is billed $10.00 (2× input) — NOT handled here.
# We only issue 5m ephemeral writes; revisit CACHE_WRITE_5M if 1h caching is ever
# adopted (it would need a separate constant + a way to tell the two writes apart).
INPUT = 5.0
OUTPUT = 25.0
CACHE_WRITE_5M = 6.25
CACHE_READ = 0.50

DEFAULT_MODEL = "claude-opus-4-8"


def _get(usage, name):
    """Read a token field from an Anthropic usage object OR a dict; 0 when absent
    or None (non-cached calls and older responses omit the cache_* fields)."""
    if usage is None:
        return 0
    val = usage.get(name, 0) if isinstance(usage, dict) else getattr(usage, name, 0)
    return val or 0


def tiered_cost(usage, model=DEFAULT_MODEL):
    """Cache-aware cost in USD. The four token classes are disjoint — sum, don't
    subtract. Translation calls carry no cache tokens, so this equals the old flat
    input*5 + output*25 there; the difference shows only on the cached routing
    call, where it stops over-billing cache reads at full input price (~10×)."""
    return (
        _get(usage, "input_tokens") * INPUT
        + _get(usage, "cache_creation_input_tokens") * CACHE_WRITE_5M
        + _get(usage, "cache_read_input_tokens") * CACHE_READ
        + _get(usage, "output_tokens") * OUTPUT
    ) / 1_000_000


def record_call(ledger_path, run_id, turn_index, category, response, duration_ms):
    """Build one OTel-aligned ledger row from response.usage, append it as a
    compact JSON line to ledger_path, and RETURN the row (so the caller can use
    the cost inline and accumulate an in-memory total for the run summary).

    Crash-safe: a ledger write failure prints a warning and STILL returns the row
    — it never raises into the run."""
    usage = getattr(response, "usage", None)
    model = getattr(response, "model", None) or DEFAULT_MODEL
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "run_id": run_id,
        "turn_index": turn_index,
        "category": category,
        "model": model,
        "operation": "chat",
        "input_tokens": _get(usage, "input_tokens"),               # gen_ai.usage.input_tokens (fresh)
        "cache_creation_i_tokens": _get(usage, "cache_creation_input_tokens"),
        "cache_read_input_tokens": _get(usage, "cache_read_input_tokens"),
        "output_tokens": _get(usage, "output_tokens"),             # gen_ai.usage.output_tokens
        "cost_usd": round(tiered_cost(usage, model), 6),
        "duration_ms": int(duration_ms),
    }
    try:
        with open(ledger_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"WARNING: cost ledger write failed ({e}) — row not persisted; run continues")
    return row
