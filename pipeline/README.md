# Wobbo Bobo clip pipeline (MVP)

Phone -> Telegram -> transcribe -> pick segments + quote (plain Python) -> FFmpeg render
(9:16, word-highlighted captions, quote overlay, loudness normalized) -> TikTok inbox.
You finish the post in the TikTok app (caption + hashtags are sent to Telegram to paste).

No LLM at runtime: segments come from the word timestamps (drop leading filler,
cut pauses > 0.8s, fill up to `TARGET_MAX_S`), and the quote, caption and hashtags come
from the text files in `content/` - mounted into the container, so edits apply to the next job without a rebuild. Quotes rotate
least-recently-used with a cooldown (`QUOTE_COOLDOWN_DAYS`), logged in `data/quotes_used.log`.

No database: every job is a folder under `data/jobs/`, and each step skips if its
output already exists, so `/retry <job_id>` resumes from the failed step. Job folders (and the local Bot API
server's copies of uploads) are deleted `JOB_RETENTION_DAYS` (30) after their last activity -
the privacy policy promises this, so keep them in sync. Manual run: `python cleanup.py --dry-run`.

## Why inbox, not direct post

TikTok's Content Sharing Guidelines reject "a utility tool to help upload contents to
the account(s) you or your team manages", so a personal app won't pass the audit, and
unaudited direct posts are private-only. Upload-to-inbox (`video.upload` scope) puts the
video in your TikTok inbox. Limits: 6 init requests/min per token, max 5 pending shares
per 24h, so finish or discard drafts in the app.

## Setup

1. **TikTok app** - developers.tiktok.com -> create app, add Login Kit + Content Posting
   API, request scopes `user.info.basic` and `video.upload`, register an HTTPS redirect URI
   (any page you control - the code is read from the URL bar). While unaudited, add your
   TikTok account as a sandbox/target user.
2. **Telegram** - bot token from @BotFather, your chat id from @userinfobot, API id/hash
   from my.telegram.org for the local Bot API server.
3. `cp .env.example .env` and fill it in.
4. One-time TikTok auth (writes `data/tiktok_tokens.json`; the refresh token lasts 365 days):
   ```bash
   docker compose run --rm worker python tiktok.py auth
   ```
5. The first time you switch a bot to a local Bot API server, log it out of the cloud API:
   `curl https://api.telegram.org/bot<TOKEN>/logOut`
6. `docker compose up -d --build`

## Use

- Send a video (or an album of videos, combined in order). The caption is the quote,
  used verbatim (`text | author` for attribution); leave it empty to use `content/quotes.txt`.
- Add `fractal=0..7` anywhere in the caption to set the fractal overlay for that video
  (default `FRACTALITY` in `.env`; 5+ adds mirror symmetry, 7 adds hue cycling).
- `/jobs` - recent jobs and status. `/retry <job_id>` - resume a failed job.
- Run a job by hand: `python pipeline.py data/jobs/<id> [--publish]`.

## Not in the MVP

TTS voiceover, background music + ducking, color normalization, smart reframing
(it center-crops), Approve/Regenerate buttons, performance tracking.
