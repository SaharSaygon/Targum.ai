"""
translate_one.py — linear proof-of-concept for single-file translation.
Not an agent. Fill in DRIVE_FILE_ID and COURSE_HEBREW before running.
"""

import io
import json
import os
import re
import sys
from pathlib import Path

import anthropic
import pypdf
from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ── Fill these in ─────────────────────────────────────────────────────────────
DRIVE_FILE_ID = "1UKRi46WMrXGJyWaJPcUhC-gOnBiSXo8b"
COURSE_HEBREW = "תכנון אלגוריתמים"

PROJECT_ROOT = Path(__file__).parent
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


# ── Drive auth ────────────────────────────────────────────────────────────────

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


# ── Text quality check ────────────────────────────────────────────────────────

def has_readable_text(text: str) -> bool:
    """Return False if fewer than 50% of tokens contain Hebrew or Latin letters."""
    tokens = text.split()
    if not tokens:
        return False
    readable = sum(1 for t in tokens if re.search(r"[a-zA-Zא-ת]", t))
    return (readable / len(tokens)) >= 0.5


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")

    # Step 1 — resolve course name
    with (PROJECT_ROOT / "courses.json").open(encoding="utf-8") as f:
        courses: dict[str, str] = json.load(f)
    course_en = courses.get(COURSE_HEBREW)
    if not course_en:
        print(f"Error: {COURSE_HEBREW!r} not found in courses.json")
        print(f"Available keys: {list(courses.keys())}")
        sys.exit(1)
    print(f"Course: {COURSE_HEBREW} → {course_en}")

    # Step 2 — authenticate Drive
    print("Authenticating with Google Drive...")
    service = build("drive", "v3", credentials=get_drive_credentials())

    # Step 3 — fetch filename and download bytes
    meta = service.files().get(fileId=DRIVE_FILE_ID, fields="name").execute()
    original_filename = meta["name"]
    print(f"File: {original_filename}")

    print("Downloading PDF...", end=" ", flush=True)
    pdf_bytes = download_bytes(service, DRIVE_FILE_ID)
    print(f"{len(pdf_bytes):,} bytes.")

    # Step 4 — extract text with pypdf
    print("Extracting text...")
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    extracted = "\n\n".join(
        page.extract_text() or "" for page in reader.pages
    ).strip()

    if not extracted or not has_readable_text(extracted):
        print("Image PDF — no extractable text. Not supported yet.")
        sys.exit(0)

    print(f"Extracted {len(extracted):,} chars across {len(reader.pages)} pages.")

    # Step 5 — load system prompt
    system_prompt = (PROJECT_ROOT / "translation_system_prompt_agent.txt").read_text(
        encoding="utf-8"
    )

    # Step 6 — call Claude
    # anthropic.Anthropic() reads ANTHROPIC_API_KEY from the environment automatically.
    # No need to pass the key explicitly — load_dotenv() already put it in os.environ.
    client = anthropic.Anthropic()

    print("Calling Claude Opus 4.7 for translation (this may take a minute)...")
    try:
        response = client.messages.create(
            model="claude-opus-4-7",
            max_tokens=16000,
            system=system_prompt,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Course: {course_en}\n"
                        f"Source file: {original_filename}\n\n"
                        f"{extracted}"
                    ),
                }
            ],
        )
    except anthropic.APIError as e:
        print(f"API error: {e}")
        sys.exit(1)

    translation = response.content[0].text
    usage = response.usage
    print(
        f"Done. Input tokens: {usage.input_tokens:,} | "
        f"Output tokens: {usage.output_tokens:,}"
    )

    # Step 7 — write to vault
    vault_path = Path(os.environ["OBSIDIAN_VAULT_PATH"])
    stem = Path(original_filename).stem   # e.g. "Lecture01_abc123"
    out_path = vault_path / course_en / (stem + "_EN.md")

    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(translation, encoding="utf-8")
    except OSError as e:
        print(f"File write error: {e}")
        sys.exit(1)

    print(f"Saved → {out_path}")


if __name__ == "__main__":
    main()
