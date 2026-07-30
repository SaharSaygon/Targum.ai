"""
prepare.py — per-file deterministic preparation for the SDK path.

The code half of what legacy read_file_logic did when the model called
read_file: md5 freshness gate → download → content hash → hash dedup →
extraction signals. In Shape D Python runs it BEFORE any session is spawned, so
the route session receives ready-made signals and file bytes never enter any
model context they don't belong in.
"""

from dataclasses import dataclass, field

from core import dedup, drive, manifest
from core.pdf_mode_detector import detect_pdf_mode

_SCALAR_SIGNAL_KEYS = (
    "recognizability",
    "tokens_per_page",
    "bytes_per_token",
    "math_token_fraction",
    "max_garbage_run_DIAGNOSTIC",
    "page_count",
    "file_size_kb",
)


@dataclass
class FileContext:
    """Everything the per-file pipeline needs, held privately by that pipeline
    (no global cache: the context dies with the file's task)."""
    file_id: str
    name: str
    parent_path: list[str]
    pdf_bytes: bytes
    source_hash: str            # canonical "sha256:..." (manifest format)
    drive_md5: str | None       # None for native Google Docs
    signals: dict               # scalar signals (the route session's evidence)
    signals_full: dict = field(repr=False, default_factory=dict)  # per_page + unrecognized_sample
    modified_time: str | None = None  # Drive modifiedTime — freshness signal when md5 is None

    @property
    def path(self) -> str:
        return "/".join(self.parent_path + [self.name])


def _refresh_modified_time(entries, file_id, modified_time):
    """Update an existing entry's source_modified_time in place (atomic save).
    No-op when there is no entry for this id (cross-ID dedup hit) or nothing
    changed."""
    entry = manifest.find_by_id(entries, file_id)
    if (entry is not None and modified_time
            and entry.get("source_modified_time") != modified_time):
        entry["source_modified_time"] = modified_time
        manifest.save_log(entries)


def prepare_file(item: dict) -> dict:
    """Run the deterministic pre-session pipeline for one worklist item.

    Returns one of:
      {"status": "ready", "ctx": FileContext}
      {"status": "already_done", ...}   (md5 gate or hash dedup — verbatim verdict)
      {"status": "error", "reason": ...}
    Never raises: download/parse/detector failures come back as error dicts so
    the orchestrator can mark the file transient and move on.
    """
    file_id = item["file_id"]

    # 0. md5 freshness gate — cheap metadata call, no byte download. (The
    #    pre-pass already md5-diffed, but the gate stays as defense in depth and
    #    covers native Google Docs edge cases exactly as the legacy path did.)
    #    modifiedTime rides along in the same call: it is the freshness signal
    #    recorded for native Google files, which have no md5.
    try:
        meta = drive.file_meta(file_id)
    except Exception as e:
        return {"status": "error", "reason": f"metadata fetch failed: {e}"}
    drive_md5 = meta.get("md5Checksum")
    modified_time = meta.get("modifiedTime")
    entries = manifest.load_log()
    verdict = dedup.md5_gate(entries, file_id, drive_md5)
    if verdict is not None:
        return verdict

    # 1. download the raw bytes (integrity-checked vs Drive size)
    try:
        pdf_bytes = drive.download_bytes(file_id)
    except Exception as e:
        return {"status": "error", "reason": f"download failed: {e}"}

    # 2. content hash — identity for dedup AND the manifest's source_content_hash.
    source_hash = manifest.sha256_of(pdf_bytes)

    # 3. dedup against the manifest (by id gated on hash, cross-ID fallback).
    verdict = dedup.hash_dedup(entries, file_id, source_hash)
    if verdict.get("status") == "already_done":
        if drive_md5 is None:
            # Native Google file dismissed by content hash: refresh the stored
            # modifiedTime so the NEXT pre-pass drops it without a download —
            # without this, a Doc reappears on every run forever (no md5 gate).
            _refresh_modified_time(entries, file_id, modified_time)
        return verdict

    # 4. extraction signals — the route session's text-vs-image evidence.
    try:
        raw_signals = detect_pdf_mode(pdf_bytes)
    except Exception as e:
        return {"status": "error", "reason": f"detector failed: {e}"}

    ctx = FileContext(
        file_id=file_id,
        name=item["name"],
        parent_path=list(item["parent_path"]),
        pdf_bytes=pdf_bytes,
        source_hash=source_hash,
        drive_md5=drive_md5,
        modified_time=modified_time,
        signals={k: raw_signals[k] for k in _SCALAR_SIGNAL_KEYS},
        signals_full={
            "per_page":            raw_signals["per_page"],
            "unrecognized_sample": raw_signals["unrecognized_sample"],
        },
    )
    return {"status": "ready", "ctx": ctx}
