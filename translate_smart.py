"""
translate_smart.py — single-file translation with automatic mode detection.

Fills in DRIVE_FILE_ID, COURSE_HEBREW, and TYPE before running.
CLI arguments and agent-loop integration come in a later phase.
"""

import datetime
import io
import json
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from pdf_mode_detector import detect_pdf_mode
from translation_engine import (
    MODEL,
    sha256_of,
    translate_image_pdf,
    translate_text_pdf,
)

# ── Fill these in before running ──────────────────────────────────────────────
DRIVE_FILE_ID = "<paste Drive file ID here>"
COURSE_HEBREW = "<paste Hebrew course name here>"
TYPE = "lecture"   # one of: lecture | tutorial | homework | exam

# TODO Day 2 Part 4: agent loop will determine type semantically by reading
# the file's folder path in Drive. For manual single-file runs, set it here.
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Maps the semantic type value to the subfolder name inside the course folder.
# "lecture" → vault/<course>/Lectures/<file>_EN.md, etc.
TYPE_TO_FOLDER = {
    "lecture":  "Lectures",
    "tutorial": "Tutorials",
    "homework": "Homework",
    "exam":     "Exams",
}


# ── Drive auth ────────────────────────────────────────────────────────────────

def get_drive_credentials() -> Credentials:
    # token.json stores the access token (valid ~1 hour) and the refresh token
    # (long-lived). On first run there's no token.json, so we open a browser tab.
    # On later runs, if the access token has expired we silently refresh it.
    token_path = PROJECT_ROOT / "token.json"
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(PROJECT_ROOT / "credentials.json"), SCOPES
            )
            creds = flow.run_local_server(port=0)
        # Persist both tokens so the next run is silent.
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def download_bytes(service, file_id: str) -> bytes:
    # The Drive API streams large files in chunks rather than one HTTP response.
    # MediaIoBaseDownload handles the chunking loop; BytesIO acts as an
    # in-memory file so nothing is written to disk.
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


# ── Log helpers ───────────────────────────────────────────────────────────────

def load_log() -> list:
    log_path = PROJECT_ROOT / "translated_log.json"
    if log_path.exists():
        with open(log_path, encoding="utf-8") as f:
            return json.load(f)
    return []


def save_log(entries: list) -> None:
    log_path = PROJECT_ROOT / "translated_log.json"
    # Write to a .tmp file first, then rename atomically with os.replace().
    # os.replace() is a single filesystem operation on POSIX — if the script
    # crashes mid-write, the old log stays intact instead of being corrupted.
    tmp = log_path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        # ensure_ascii=False writes Hebrew as-is (e.g. "הרצאה") instead of
        # escaping it to \uXXXX sequences, which are valid JSON but unreadable.
        json.dump(entries, f, ensure_ascii=False, indent=2)
    os.replace(tmp, log_path)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Step 1: load config ───────────────────────────────────────────────────
    # load_dotenv reads .env and injects KEY=VALUE pairs into os.environ.
    # It won't overwrite keys that are already set in the environment.
    load_dotenv(PROJECT_ROOT / ".env")

    with (PROJECT_ROOT / "courses.json").open(encoding="utf-8") as f:
        courses: dict[str, str] = json.load(f)

    # .get() returns None if the key is missing, instead of raising KeyError.
    course_english = courses.get(COURSE_HEBREW)
    if not course_english:
        print(f"Error: {COURSE_HEBREW!r} not in courses.json")
        print(f"Available: {list(courses.keys())}")
        sys.exit(1)

    if TYPE not in TYPE_TO_FOLDER:
        print(f"Error: TYPE={TYPE!r} must be one of {list(TYPE_TO_FOLDER)}")
        sys.exit(1)

    # ── Step 2: auth Drive, fetch filename, download ──────────────────────────
    print("Authenticating with Google Drive...")
    service = build("drive", "v3", credentials=get_drive_credentials())

    # fields="name" limits the API response to just the filename.
    # Without this, Drive returns the full metadata object — wasteful.
    meta = service.files().get(fileId=DRIVE_FILE_ID, fields="name").execute()
    drive_filename = meta["name"]
    print(f"File: {drive_filename}")

    print("Downloading...", end=" ", flush=True)
    pdf_bytes = download_bytes(service, DRIVE_FILE_ID)
    print(f"{len(pdf_bytes):,} bytes.")

    # ── Step 3: hash and dedup ────────────────────────────────────────────────
    # We hash the *content* (not the Drive ID) so if the same PDF is re-uploaded
    # with a new ID or name, we still catch it as a duplicate.
    source_hash = sha256_of(pdf_bytes)
    log = load_log()

    for entry in log:
        if entry.get("source_content_hash") == source_hash:
            # Non-null md_path means we have a completed translation on disk.
            # Before running Test 2 (L4 redo), manually set md_path to null
            # in translated_log.json so this guard doesn't fire.
            if entry.get("md_path") is not None:
                print(f"Already done: {entry['md_path']}")
                sys.exit(0)
            # skipped_permanent means a previous run decided this file should
            # never be translated (e.g. answer key, irrelevant file).
            if entry.get("model") == "skipped_permanent":
                print(f"Skipped permanently: {entry.get('skip_reason', '')}")
                sys.exit(0)

    # ── Step 4: detect PDF mode ───────────────────────────────────────────────
    print("\nRunning PDF mode detector...")
    detection = detect_pdf_mode(pdf_bytes)

    # Print the full dict so the operator can see the metrics before any API call.
    print("Detection result:")
    for k, v in detection.items():
        print(f"  {k:<20} {v}")
    print()

    # ── Step 5: translate ─────────────────────────────────────────────────────
    today_date = datetime.date.today().isoformat()   # e.g. "2026-05-18"
    t_start = time.monotonic()

    try:
        if detection["mode"] == "text":
            print(f"Mode: text — calling {MODEL} with extracted text...")
            result = translate_text_pdf(
                pdf_bytes, course_english, DRIVE_FILE_ID,
                drive_filename, source_hash, today_date,
            )
        else:
            print(f"Mode: image — calling {MODEL} with page images...")
            result = translate_image_pdf(
                pdf_bytes, course_english, DRIVE_FILE_ID,
                drive_filename, source_hash, today_date,
            )
    except anthropic.APIError as e:
        print(f"API error: {e}")
        sys.exit(1)
    except RuntimeError as e:
        print(f"Translation error: {e}")
        sys.exit(1)

    duration = time.monotonic() - t_start

    # ── Step 6: save markdown to vault ───────────────────────────────────────
    vault_path = Path(os.environ["OBSIDIAN_VAULT_PATH"])

    # Build the save path:
    #   <vault>/<course_english>/<type_folder>/<stem>_EN.md
    # Path.stem strips the extension: "הרצאה 4.pdf" → "הרצאה 4"
    stem = Path(drive_filename).stem
    subfolder = TYPE_TO_FOLDER[TYPE]
    out_path = vault_path / course_english / subfolder / f"{stem}_EN.md"

    # parents=True creates intermediate dirs (like mkdir -p).
    # exist_ok=True suppresses the error if the dir already exists.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result["markdown"], encoding="utf-8")
    print(f"Saved → {out_path}")

    # Vault-relative path stored in log so the record stays valid
    # if the vault root ever moves.
    md_path_relative = str(out_path.relative_to(vault_path))

    # ── Step 7: update translated_log.json ───────────────────────────────────
    new_entry = {
        "drive_file_id":        DRIVE_FILE_ID,
        "drive_file_name":      drive_filename,
        "source_content_hash":  source_hash,
        "md_path":              md_path_relative,
        "course":               course_english,
        "type":                 TYPE,           # semantic value: "lecture", not "Lectures"
        "translated_at":        datetime.datetime.now(datetime.timezone.utc)
                                    .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model":                result["model"],
        "cost_usd":             result["cost_usd"],
        "input_tokens":         result["input_tokens"],
        "output_tokens":        result["output_tokens"],
        # Detection metrics — logged every run for later threshold retuning.
        "detection_mode":       detection["mode"],
        "recognizability":      detection["recognizability"],
        "max_garbage_run":      detection["max_garbage_run"],
        "total_tokens":         detection["total_tokens"],
        "detection_reason":     detection["reason"],
    }

    # Find the existing entry for this Drive file ID, if any.
    # next() with a default of None means: "return the first match, or None".
    # enumerate() gives us both the index (i) and the entry (e) so we can
    # update in place instead of appending a duplicate.
    idx = next(
        (i for i, e in enumerate(log) if e.get("drive_file_id") == DRIVE_FILE_ID),
        None,
    )
    if idx is not None:
        log[idx] = new_entry   # overwrite the bad/old entry in place
    else:
        log.append(new_entry)  # first time we've seen this file

    save_log(log)
    print("Log updated → translated_log.json")

    # ── Step 8: summary ───────────────────────────────────────────────────────
    rec_pct = f"{detection['recognizability']:.0%}"
    print(
        f"\n{'='*50}\n"
        f"TRANSLATION SUMMARY\n"
        f"Source:        {drive_filename}\n"
        f"Mode:          {result['mode']}\n"
        f"Detection:     {rec_pct} recognizable, "
            f"{detection['max_garbage_run']}-token max garbage run\n"
        f"Course:        {course_english}\n"
        f"Type:          {TYPE}\n"
        f"Output:        {md_path_relative}\n"
        f"Input tokens:  {result['input_tokens']:,}\n"
        f"Output tokens: {result['output_tokens']:,}\n"
        f"Cost:          ${result['cost_usd']:.4f}\n"
        f"Duration:      {duration:.1f}s\n"
        f"{'='*50}"
    )


if __name__ == "__main__":
    # Guard so main() only runs when the script is executed directly,
    # not when it's imported by tests or other scripts.
    main()
