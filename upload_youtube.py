#!/usr/bin/env python3
"""
Upload today's reel to YouTube as a Short.

One-time setup (~5 min):
  1. console.cloud.google.com → new project → enable "YouTube Data API v3"
  2. OAuth consent screen → External → add yourself as test user
  3. Credentials → Create OAuth client ID → Desktop app →
     download JSON as yt_client_secret.json into this folder
  4. Run `python3 upload_youtube.py --auth` once — a browser opens,
     sign in, approve. The token (yt_token.json) refreshes itself after that.

Daily use (called by daily_run.sh):
    python3 upload_youtube.py                  # uploads reels/reel_<today>.mp4
    python3 upload_youtube.py --video x.mp4 --caption y.txt
"""

import argparse
import os
import sys
from datetime import date

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REELS_DIR = os.path.join(BASE_DIR, "reels")
CLIENT_SECRET = os.path.join(BASE_DIR, "yt_client_secret.json")
TOKEN = os.path.join(BASE_DIR, "yt_token.json")
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def get_service(interactive):
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        sys.exit("Missing deps: pip3 install --user "
                 "google-api-python-client google-auth-oauthlib")

    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN, "w") as f:
                f.write(creds.to_json())
        except Exception as e:
            # testing-mode refresh tokens die after 7 days -> invalid_grant
            creds = None
            if not interactive:
                sys.exit(f"TOKEN EXPIRED ({e}) — run `python3 upload_youtube.py "
                         "--auth` to re-authorize (and publish the Google app "
                         "to production to stop weekly expiry).")
    if not creds or not creds.valid:
        if not interactive:
            sys.exit("Not authorized yet — run `python3 upload_youtube.py --auth` "
                     "once (needs a browser). See setup steps in this file.")
        if not os.path.exists(CLIENT_SECRET):
            sys.exit(f"Missing {CLIENT_SECRET} — see setup steps in this file.")
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
        creds = flow.run_local_server(port=0)
        with open(TOKEN, "w") as f:
            f.write(creds.to_json())
        # testing-mode refresh tokens die 7 days after ISSUE (refreshing does
        # not extend); daily_run.sh warns when this marker turns 6 days old
        with open(TOKEN + ".issued", "w") as f:
            f.write(str(int(__import__("time").time())))
        print("Authorized — token saved. Daily uploads will work unattended now.")
    return build("youtube", "v3", credentials=creds)


def main():
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("--auth", action="store_true",
                    help="run the one-time browser authorization")
    ap.add_argument("--video", help="video file (default: today's reel)")
    ap.add_argument("--caption", help="caption file (default: today's caption)")
    args = ap.parse_args()

    yt = get_service(interactive=args.auth)
    if args.auth and not args.video:
        issued = os.path.join(BASE_DIR, "yt_token.json.issued")
        if os.path.exists(issued):
            import time
            age = (time.time() - int(open(issued).read().strip())) / 86400
            print(f"Token is valid (issued {age:.1f} days ago; "
                  f"testing-mode tokens expire at 7 days).")
        else:
            print("Token is valid.")
        return  # auth-only run

    stamp = date.today().isoformat()
    video = args.video or os.path.join(REELS_DIR, f"reel_{stamp}_top.mp4")
    caption = args.caption or os.path.join(REELS_DIR, f"caption_{stamp}_top.txt")
    if not os.path.exists(video):
        sys.exit(f"No reel found at {video} — run make_reel.py first.")

    desc = open(caption).read() if os.path.exists(caption) else ""
    title = (desc.splitlines()[0] if desc else
             f"Top Amazon Deals Today {stamp} #shorts")[:95]

    from googleapiclient.http import MediaFileUpload
    request = yt.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": title,
                "description": desc,
                "categoryId": "22",
                "tags": ["amazon deals", "deals india", "amazon finds", "shorts"],
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        },
        media_body=MediaFileUpload(video, chunksize=-1, resumable=True),
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"upload {int(status.progress() * 100)}%")
    print(f"Published: https://youtube.com/shorts/{response['id']}")


if __name__ == "__main__":
    main()
