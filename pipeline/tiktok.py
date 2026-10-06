"""TikTok Content Posting API - upload to the creator's inbox (drafts), scope video.upload.

One-time auth:  python tiktok.py auth                 (interactive)
           or:  python tiktok.py auth-url             then
                python tiktok.py auth-code '<redirected URL>'
Manual upload:  python tiktok.py upload path/to/video.mp4
"""

import json
import secrets
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

import config

API = "https://open.tiktokapis.com"
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
SCOPES = "user.info.basic,video.upload"
CHUNK = 10 * 1024 * 1024  # docs: 5MB-64MB per chunk, last chunk up to 128MB


class TikTokError(RuntimeError):
    pass


# --- tokens -----------------------------------------------------------------

def _save(tok: dict) -> None:
    tok["expires_at"] = time.time() + tok["expires_in"] - 300
    config.TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
    config.TOKENS_FILE.write_text(json.dumps(tok, indent=2))
    config.TOKENS_FILE.chmod(0o600)


def _token_request(data: dict) -> dict:
    r = httpx.post(
        f"{API}/v2/oauth/token/",
        data={"client_key": config.TIKTOK_CLIENT_KEY, "client_secret": config.TIKTOK_CLIENT_SECRET, **data},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    body = r.json()
    if r.status_code != 200 or "access_token" not in body:
        raise TikTokError(f"token request failed: {body}")
    return body


def access_token() -> str:
    if not config.TOKENS_FILE.exists():
        raise TikTokError("not authorized - run `python tiktok.py auth` once")
    tok = json.loads(config.TOKENS_FILE.read_text())
    if time.time() >= tok["expires_at"]:
        # Refresh tokens last 365 days and may rotate - always store the new one.
        tok = _token_request({"grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
        _save(tok)
    return tok["access_token"]


def connected() -> bool:
    return config.TOKENS_FILE.exists()


STATE_FILE = config.DATA_DIR / "tiktok_auth_state"


def auth_url() -> str:
    state = secrets.token_urlsafe(16)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(state)
    return AUTH_URL + "?" + urllib.parse.urlencode({
        "client_key": config.TIKTOK_CLIENT_KEY,
        "scope": SCOPES,
        "response_type": "code",
        "redirect_uri": config.TIKTOK_REDIRECT_URI,
        "state": state,
    })


def auth_code(redirected: str) -> dict:
    q = urllib.parse.parse_qs(urllib.parse.urlparse(redirected.strip()).query)
    expected = STATE_FILE.read_text() if STATE_FILE.exists() else None
    if not expected or q.get("state", [None])[0] != expected:
        sys.exit("state mismatch - run auth-url again and use the newest link")
    if "code" not in q:
        sys.exit(f"no code in redirect: {q}")
    tok = _token_request({
        "code": q["code"][0],
        "grant_type": "authorization_code",
        "redirect_uri": config.TIKTOK_REDIRECT_URI,
    })
    _save(tok)
    STATE_FILE.unlink(missing_ok=True)
    return tok


def auth_cli() -> None:
    print("Open this URL, log in, approve, then paste the full URL you were redirected to:\n")
    print(auth_url(), "\n")
    tok = auth_code(input("Redirected URL: "))
    print(f"Saved tokens to {config.TOKENS_FILE} (scopes: {tok.get('scope')})")


# --- upload -----------------------------------------------------------------

def _api(path: str, body: dict) -> dict:
    r = httpx.post(
        f"{API}{path}",
        json=body,
        headers={"Authorization": f"Bearer {access_token()}", "Content-Type": "application/json; charset=UTF-8"},
        timeout=30,
    )
    data = r.json()
    if data.get("error", {}).get("code", "ok") != "ok":
        raise TikTokError(f"{path}: {data['error']}")
    return data["data"]


def upload_to_inbox(video: Path) -> str:
    """Upload a finished video to the creator's TikTok inbox. Returns publish_id."""
    size = video.stat().st_size
    if size <= 64 * 1024 * 1024:
        chunk, count = size, 1  # docs: <5MB must be one chunk; up to 64MB may be
    else:
        chunk = CHUNK
        count = size // chunk  # docs: round down; the last chunk absorbs the remainder
    data = _api("/v2/post/publish/inbox/video/init/", {
        "source_info": {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count},
    })
    publish_id, upload_url = data["publish_id"], data["upload_url"]

    with video.open("rb") as f, httpx.Client(timeout=300) as http:
        for i in range(count):
            first = i * chunk
            last = size - 1 if i == count - 1 else first + chunk - 1
            f.seek(first)
            payload = f.read(last - first + 1)
            r = http.put(upload_url, content=payload, headers={
                "Content-Type": "video/mp4",
                "Content-Length": str(len(payload)),
                "Content-Range": f"bytes {first}-{last}/{size}",
            })
            if r.status_code not in (201, 206):
                raise TikTokError(f"chunk {i + 1}/{count} failed: HTTP {r.status_code} {r.text[:300]}")
    return publish_id


def wait_for_inbox(publish_id: str, timeout_s: int = 300) -> str:
    """Poll until TikTok has delivered the draft (or failed). Returns the final status."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = _api("/v2/post/publish/status/fetch/", {"publish_id": publish_id})
        status = data.get("status")
        if status == "FAILED":
            raise TikTokError(f"TikTok processing failed: {data.get('fail_reason')}")
        if status in ("SEND_TO_USER_INBOX", "PUBLISH_COMPLETE"):
            return status
        time.sleep(5)  # status endpoint allows 30 req/min
    return "TIMEOUT"


if __name__ == "__main__":
    if sys.argv[1:2] == ["auth"]:
        auth_cli()
    elif sys.argv[1:2] == ["auth-url"]:
        print(auth_url())
    elif sys.argv[1:2] == ["auth-code"] and len(sys.argv) == 3:
        tok = auth_code(sys.argv[2])
        print(f"Saved tokens to {config.TOKENS_FILE} (scopes: {tok.get('scope')})")
    elif sys.argv[1:2] == ["upload"] and len(sys.argv) == 3:
        pid = upload_to_inbox(Path(sys.argv[2]))
        print(pid, wait_for_inbox(pid))
    else:
        sys.exit(__doc__)
