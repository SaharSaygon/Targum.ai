"""
translate_image_pdf.py — thin wrapper for manual single-file image-mode translation.
Fill in DRIVE_FILE_ID and COURSE_HEBREW, then run directly.
All translation logic lives in translation_engine.py.
"""

import datetime
import io
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from manifest import load_log, save_log
from translation_engine import infer_type, sha256_of, translate_image_pdf

# ── Fill these in ─────────────────────────────────────────────────────────────
DRIVE_FILE_ID = "1wW1mT9Ab1C-x6cdGLOrndkU_82JQNjuP"
COURSE_HEBREW = "מבוא למערכות לינאריות"
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


def get_drive_credentials() -> Credentials:
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
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def download_bytes(service, file_id: str) -> bytes:
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return buf.getvalue()


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    with (PROJECT_ROOT / "courses.json").open(encoding="utf-8") as f:
        courses: dict[str, str] = json.load(f)
    course_english = courses.get(COURSE_HEBREW)
    if not course_english:
        print(f"Error: {COURSE_HEBREW!r} not in courses.json")
        sys.exit(1)
    print(f"Course: {COURSE_HEBREW} → {course_english}")

    print("Authenticating with Google Drive...")
    service = build("drive", "v3", credentials=get_drive_credentials())

    meta = service.files().get(fileId=DRIVE_FILE_ID, fields="name").execute()
    drive_filename = meta["name"]
    print(f"File: {drive_filename}")

    print("Downloading...", end=" ", flush=True)
    pdf_bytes = download_bytes(service, DRIVE_FILE_ID)
    print(f"{len(pdf_bytes):,} bytes.")

    # Hash-based dedup: skip if this exact content was already translated.
    source_hash = sha256_of(pdf_bytes)
    log = load_log()
    for entry in log:
        if entry.get("source_content_hash") == source_hash:
            if entry.get("md_path") is not None or entry.get("model") == "skipped_permanent":
                print(f"Already done: {entry.get('md_path') or entry.get('skip_reason')}")
                sys.exit(0)

    today_date = datetime.date.today().isoformat()

    print(f"Calling translation engine (image mode)...")
    try:
        result = translate_image_pdf(
            pdf_bytes, course_english, DRIVE_FILE_ID,
            drive_filename, source_hash, today_date,
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

    vault_path = Path(os.environ["OBSIDIAN_VAULT_PATH"])
    stem = Path(drive_filename).stem
    out_path = vault_path / course_english / (stem + "_EN.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(result["markdown"], encoding="utf-8")
    print(f"Saved → {out_path}")

    log = [e for e in log if e.get("drive_file_id") != DRIVE_FILE_ID]
    log.append({
        "drive_file_id":       DRIVE_FILE_ID,
        "drive_file_name":     drive_filename,
        "source_content_hash": source_hash,
        "md_path":             str(out_path.relative_to(vault_path)),
        "course":              course_english,
        "type":                infer_type(drive_filename),
        "translated_at":       datetime.datetime.now(datetime.timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model":               result["model"],
        "cost_usd":            result["cost_usd"],
        "input_tokens":        result["input_tokens"],
        "output_tokens":       result["output_tokens"],
    })
    save_log(log)

    print(
        f"Done. Input: {result['input_tokens']:,} | "
        f"Output: {result['output_tokens']:,} | "
        f"Cost: ${result['cost_usd']:.4f}"
    )
    print("Log updated → translated_log.json")


if __name__ == "__main__":
    main()
