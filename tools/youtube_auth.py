#!/usr/bin/env python3
"""Make secrets/youtube_token.json, the server's login for uploading timelapses.

Run on a computer with a web browser (not the server):

  pip install google-auth google-auth-oauthlib google-api-python-client

  # Option A: reuse the Raspberry Pi's old login (credentials.storage)
  python tools/youtube_auth.py --from-storage credentials.storage

  # Option B: sign in again in the browser, using the Google Cloud OAuth
  # client file (client_secret.json, type "Desktop app")
  python tools/youtube_auth.py --client-secret client_secret.json

Both check the login works by asking YouTube for the channel name, then write
youtube_token.json. Copy that file to /opt/birbilling-ws/secrets/ on the server.

If Google asks again every 7 days, the Google Cloud project's OAuth consent
screen is still in "Testing": set it to "In production" (Publish app).
"""
import argparse
import json
import sys

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.readonly"]
TOKEN_URI = "https://oauth2.googleapis.com/token"


def from_storage(path: str) -> dict:
    """oauth2client's Storage file (used by the old Pi script) -> authorized_user."""
    old = json.load(open(path))
    missing = [k for k in ("client_id", "client_secret", "refresh_token") if not old.get(k)]
    if missing:
        sys.exit(f"{path} has no {', '.join(missing)}; use --client-secret instead")
    return {"type": "authorized_user", "client_id": old["client_id"],
            "client_secret": old["client_secret"], "refresh_token": old["refresh_token"],
            "token_uri": TOKEN_URI}


def from_browser(client_secret: str) -> dict:
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    return json.loads(creds.to_json())


def check(info: dict) -> None:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_info(info)
    try:
        creds.refresh(Request())
    except Exception as exc:
        sys.exit(f"Login doesn't work ({exc}). Use --client-secret to sign in again.")
    try:
        yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
        print("Logged in to channel:", items[0]["snippet"]["title"] if items else "(none found)")
    except Exception as exc:  # an old login may only allow uploads, not reading
        print("Login refreshed; couldn't read the channel name:", exc)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--from-storage", metavar="credentials.storage")
    g.add_argument("--client-secret", metavar="client_secret.json")
    ap.add_argument("--out", default="youtube_token.json")
    args = ap.parse_args()

    info = from_storage(args.from_storage) if args.from_storage else from_browser(args.client_secret)
    check(info)
    with open(args.out, "w") as f:
        json.dump(info, f, indent=2)
    print("Wrote", args.out)


if __name__ == "__main__":
    main()
