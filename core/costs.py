"""Cache-aware per-call cost + token ledger. Stdlib only — no anthropic, no
drive, no client; importable bare (no API key, no OAuth).

One recorder, record_call(), runs after every messages.create at the two LLM
call sites (routing in agent.py; translation in translation_engine via an
on_usage callback). It computes a cache-TIERED cost, appends an OTel-aligned
JSON line to the run ledger, and returns the row so the caller can roll it up.
"""
import json
from datetime import datetime, timezone

# Per-model pricing — USD per 1e6 tokens, as (input, output, cache_write_5m,
# cache_read). The four token classes are DISJOINT in the Anthropic usage object
# (input_tokens already EXCLUDES the cached ones), so each is multiplied by its
# own rate and the products are summed — never subtract one class from another.
#   input        fresh / uncached input
#   output       generated output
#   cache_write  5-minute ephemeral cache WRITE      (1.25× input)
#   cache_read   cache READ / hit                    (0.1× input, except where
#                                                     the model's list price differs)
# CAVEAT: a ttl:"1h" cache WRITE is billed 2× input — NOT handled here. We only
# issue 5m ephemeral writes; revisit if 1h caching is ever adopted.
# Keys are model-id prefixes, so dated snapshots (claude-haiku-4-5-20251001) and
# the bare alias share a row; the longest matching prefix wins (claude-opus-5-5
# before claude-opus-5).
PRICES = {
    "claude-fable-5-1":  (10.00, 50.00, 12.50, 0.25),
    "claude-fable-5":    (10.00, 50.00, 12.50, 1.00),
    "claude-opus-5-5":   (4.00,  20.00, 5.00,  0.20),
    "claude-opus-5":     (5.00,  25.00, 6.25,  0.50),
    "claude-opus-4-8":   (5.00,  25.00, 6.25,  0.50),
    "claude-sonnet-5":   (2.00,  10.00, 2.50,  0.20),
    "claude-sonnet-4-6": (3.00,  15.00, 3.75,  0.30),
    "claude-haiku-4-5":  (1.00,  5.00,  1.25,  0.10),
}

DEFAULT_MODEL = "claude-opus-5"
_warned = set()


def prices_for(model):
    """(input, output, cache_write_5m, cache_read) per 1e6 tokens for `model`.
    An unknown id falls back to DEFAULT_MODEL's rates with a one-time warning,
    so a new model never crashes the run — only its dollar figures are approximate."""
    m = (model or DEFAULT_MODEL).removeprefix("subscription:")
    hits = [k for k in PRICES if m == k or m.startswith(k + "-") or m.startswith(k + "@")]
    if hits:
        return PRICES[max(hits, key=len)]
    if m not in _warned:
        _warned.add(m)
        print(f"WARNING: no pricing for model {m!r} — costing it at {DEFAULT_MODEL} rates")
    return PRICES[DEFAULT_MODEL]


def _get(usage, name):
    """Read a token field from an Anthropic usage object OR a dict; 0 when absent
    or None (non-cached calls and older responses omit the cache_* fields)."""
    if usage is None:
        return 0
    val = usage.get(name, 0) if isinstance(usage, dict) else getattr(usage, name, 0)
    return val or 0


def tiered_cost(usage, model=DEFAULT_MODEL):
    """Cache-aware cost in USD at `model`'s rates (PRICES). The four token
    classes are disjoint — sum, don't subtract. Translation calls carry no cache tokens, so this equals the old flat
    input*5 + output*25 there; the difference shows only on the cached routing
    call, where it stops over-billing cache reads at full input price (~10×)."""
    inp, out, cache_write, cache_read = prices_for(model)
    return (
        _get(usage, "input_tokens") * inp
        + _get(usage, "cache_creation_input_tokens") * cache_write
        + _get(usage, "cache_read_input_tokens") * cache_read
        + _get(usage, "output_tokens") * out
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
