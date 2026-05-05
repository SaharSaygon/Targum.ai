from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import os

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
FOLDER_ID = "1FoM5o24yoBJuvpdtoZ0waJGosS4wef6V"


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


def list_files(folder_id):
    service = build("drive", "v3", credentials=get_credentials())
    query = f"'{folder_id}' in parents and trashed = false"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    if not files:
        print("No files found.")
    else:
        print(f"{'NAME':<50} ID")
        print("-" * 80)
        for f in files:
            print(f"{f['name']:<50} {f['id']}")


if __name__ == "__main__":
    list_files(FOLDER_ID)
