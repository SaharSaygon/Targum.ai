"""config.py — per-user configuration loader.

Reads config.json (gitignored, per-user) into a single typed Config object.
Secrets (ANTHROPIC_API_KEY) stay in .env and are read there, not here; this
module holds only NON-secret config. A future UI edits config.json via
save_config().

Required keys (root_folder_id, vault_path) have NO defaults — they must be set
explicitly. Optional keys (model, spend_cap_usd, tool_call_budget) fall back to
the defaults below when absent.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent / "config.json"

# Optional keys → their defaults. Required keys are deliberately absent here:
# they must be present in config.json, with no silent fallback.
_DEFAULTS = {
    "model": "claude-opus-4-8",
    "spend_cap_usd": 5.00,
    "tool_call_budget": 200,
}

# Required keys must be present and correctly typed (and non-empty for strings).
_REQUIRED = {
    "root_folder_id": str,
    "vault_path": str,
}

# Optional keys: validated against these types if present, else defaulted.
# (bool is rejected explicitly below — it's a subclass of int in Python.)
_OPTIONAL = {
    "model": str,
    "spend_cap_usd": (int, float),
    "tool_call_budget": int,
}


class ConfigError(Exception):
    """config.json is missing, unreadable, or malformed."""


@dataclass(frozen=True)
class Config:
    root_folder_id: str
    vault_path: str
    model: str
    spend_cap_usd: float
    tool_call_budget: int


def _type_names(typ) -> str:
    if isinstance(typ, tuple):
        return " or ".join(t.__name__ for t in typ)
    return typ.__name__


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Load and validate config.json into a typed Config.

    Raises ConfigError with an actionable message on a missing file, invalid
    JSON, a missing/empty required key, or a wrong-typed value.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(
            f"No config file at {path}. Copy config.example.json to config.json "
            f"and fill in your values:\n    cp config.example.json config.json"
        )

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path} is not valid JSON: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path} must contain a JSON object, got {type(raw).__name__}."
        )

    values: dict = {}

    # Required: present, right type, non-empty.
    for key, typ in _REQUIRED.items():
        if key not in raw:
            raise ConfigError(
                f"Required key '{key}' is missing from {path}. "
                f"See config.example.json for the expected shape."
            )
        val = raw[key]
        if isinstance(val, bool) or not isinstance(val, typ):
            raise ConfigError(
                f"Key '{key}' in {path} must be {_type_names(typ)}, "
                f"got {type(val).__name__}."
            )
        if isinstance(val, str) and not val.strip():
            raise ConfigError(
                f"Required key '{key}' in {path} must not be empty."
            )
        values[key] = val

    # Optional: validate if present, else default. (Unknown keys, e.g. "_note"
    # in the example file, are ignored.)
    for key, typ in _OPTIONAL.items():
        if key in raw:
            val = raw[key]
            if isinstance(val, bool) or not isinstance(val, typ):
                raise ConfigError(
                    f"Key '{key}' in {path} must be {_type_names(typ)}, "
                    f"got {type(val).__name__}."
                )
            values[key] = val
        else:
            values[key] = _DEFAULTS[key]

    # Stable type: spend_cap_usd is always a float even if written as an int.
    values["spend_cap_usd"] = float(values["spend_cap_usd"])

    return Config(**values)


def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None:
    """Persist a Config back to config.json (atomic write).

    Provided for the future UI to call when the user changes a value — e.g.
    picks a different Drive folder. The agent run does NOT call this today; the
    per-run folder override seam (agent.run_agent) leaves persistence to the UI.
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(asdict(cfg), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)
