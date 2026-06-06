# drive.py — all Google Drive logic lives here (concern-split from the agent loop).
# Auth is shared from the original list_drive.py pattern. The service object is
# built ONCE at module load — re-authing per call would spawn browser prompts
# mid-run. Only what list_folder needs lives here for now; download/sha256
# helpers come later.
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_MIME = "application/vnd.google-apps.folder"

# Transient errors (dropped sockets, SSL resets, 5xx) over a long run must not
# kill the loop. googleapiclient retries these with exponential backoff when
# .execute(num_retries=...) is set. BrokenPipeError is an OSError subclass and
# is covered by that retry path.
_NUM_RETRIES = 5


def get_credentials():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return creds


# Built lazily on first use (not at import) so `import drive` doesn't trigger
# OAuth — keeps Drive logic importable without credentials (tests, pre-pass).
# Built ONCE then reused for every call — no re-auth mid-run.
_service = None


def get_service():
    global _service
    if _service is None:
        _service = build("drive", "v3", credentials=get_credentials())
    return _service


def download_bytes(file_id):
    """Download a Drive file's raw bytes, VERIFIED COMPLETE against Drive's own
    `size` metadata. Reuses the module-level service — no re-auth.

    Why the verify loop: a dropped connection can make get_media().execute()
    return SILENTLY TRUNCATED bytes — it succeeds with a short body and raises
    nothing, so execute(num_retries=…) never re-attempts (that retry only covers
    exceptions raised *during* the request). Truncated bytes then get hashed and
    translated, silently corrupting the vault (the run-2 casualty). So we compare
    the downloaded length to Drive's reported size and re-download on mismatch,
    raising if it never completes — the caller (read_file_logic) turns the raise
    into status:'error' rather than translating a partial file."""
    meta = get_service().files().get(
        fileId=file_id, fields="size"
    ).execute(num_retries=_NUM_RETRIES)
    size = meta.get("size")
    expected = int(size) if size is not None else None  # Google-native files lack size

    last_len = None
    for _ in range(_NUM_RETRIES + 1):
        data = get_service().files().get_media(fileId=file_id).execute(num_retries=_NUM_RETRIES)
        # expected is None only for non-binary Google files (never our PDFs) —
        # nothing to verify against, so accept.
        if expected is None or len(data) == expected:
            return data
        last_len = len(data)  # short read → loop and re-download
    raise IOError(
        f"download_bytes: {file_id} truncated after {_NUM_RETRIES + 1} attempts — "
        f"got {last_len} bytes, Drive reports {expected}"
    )


def file_md5(file_id):
    """Drive's md5Checksum for a file — a content-derived checksum that changes
    ONLY when the bytes change (sync-immune, unlike modifiedTime which churns on
    the synced notebook folder). Cheap metadata call, NO byte download. Returns
    None for native Google Docs (Sheets/Docs/Slides have no md5Checksum) — our
    corpus is all binary PDFs, so that's the rare fall-through case."""
    meta = get_service().files().get(
        fileId=file_id, fields="md5Checksum"
    ).execute(num_retries=_NUM_RETRIES)
    return meta.get("md5Checksum")  # None → native Google file → gate N/A


def list_folder_children(folder_id, include_md5=False):
    """Direct children of a Drive folder — NOT recursive. The agent walks the
    tree itself via repeated calls. Paginates so 100+ file folders don't truncate.
    Returns [{name, id, type, mime_type}] where type is "folder" or "file".

    include_md5=True additionally requests md5Checksum and adds it to each child
    dict (None for folders / native Google files). Used by the deterministic
    pre-pass (prepass.py) to diff bytes vs the manifest WITHOUT downloading. The
    agent's list_folder tool leaves it False, so its tool result is unchanged."""
    fields_files = "id, name, mimeType" + (", md5Checksum" if include_md5 else "")
    query = f"'{folder_id}' in parents and trashed = false"
    children = []
    page_token = None
    while True:
        resp = get_service().files().list(
            q=query,
            fields=f"nextPageToken, files({fields_files})",
            pageSize=100,
            pageToken=page_token,
        ).execute(num_retries=_NUM_RETRIES)
        for f in resp.get("files", []):
            mime = f["mimeType"]
            child = {
                "name": f["name"],
                "id": f["id"],
                "type": "folder" if mime == FOLDER_MIME else "file",
                "mime_type": mime,
            }
            if include_md5:
                child["md5Checksum"] = f.get("md5Checksum")  # None for folders/native
            children.append(child)
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return children
