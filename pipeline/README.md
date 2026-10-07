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
  used verbatim (`text | author` for attribution). Leave it empty and the top line is a one-line
  summary: the punchiest 3-9 word phrase said in the kept footage (no LLM); clips without speech
  fall back to `content/quotes.txt`.
- Every clip opens with a hook - glitch and/or shake for the first `hook_s` seconds (`hook`,
  `hook_strength` in `style.toml`).
- Send plain text, or a voice message (transcribed locally), instead of a video for a text-only
  clip. A voice message keeps your own voice (trimmed and levelled); typed text is read by Kokoro,
  an open neural voice running in the worker (`kokoro_voice`, `kokoro_speed`). The captions follow the speech word by word over the background (`look`: `drift` - the default, a Mandelbrot dive ported from
  the Fractal Drift page with its styles and targets; `neon` - glowing blackletter card, then a neon
  fractal; `pixel` - yellow/blue pixel card). No quote on top unless you add a line `quote: ...`.
  Music is new per clip (`audio`): `ambient` - a meditative MIDI bed (pad drone, slow chords, bells,
  binaural sub) rendered with FluidSynth, the default - or `gen` (synth bassline + arpeggio), `bass`,
  `drone`, `off`. TikTok flagged the identical bassline the earlier clips all shared.
- Text and voice clips cut 1-4 random scenes together (`drift_mix`, default on): dives, slow warped spins,
  Julia sets morphing along the cardioid edge, some mirrored, joined with random transitions. Send just
  `fractal` for a wordless 14-20s mix of 4-6 scenes, about half of them slow rotations through a
  27%-warped set. Everything is seeded by the job id, so `/retry` renders the same clip.
- Kokoro (Apache 2.0) downloads ~350MB into `data/models/kokoro` on first use. `tts=say` uses a
  macOS voice instead, which runs on the Mac, not in Docker: keep `python3 tts_host.py` running
  (stdlib, 127.0.0.1:8765; reached at `TTS_URL`, default `http://host.docker.internal:8765`).
- Style and effects - fonts, colours, caption sizes, fractal overlay level, zoom, mirror, hue
  cycling, audio, voiceover - live in `content/style.toml` (edits apply to the next job). Override any key
  for one job with `key=value` in the text or caption, e.g. `fractal=5` or `pace=0.3 hue_cycle_s=4`;
  flags are stripped from the captions, and an unknown flag is rejected before anything is queued.
- Logs: `data/bot.log` - one line per event (queued with kind and flags, each step, render time
  and size, upload result, failures as `Type: message @ file:line`); rotates at 1MB, 2 backups.
  Full tracebacks stay in `docker compose logs worker`. No hook or quote text is logged.
- `/jobs` - recent jobs and status. `/retry <job_id>` - resume a failed job.
- Run a job by hand: `python pipeline.py data/jobs/<id> [--publish]`.

## Not in the MVP

Voiceover and music on video clips, ducking, color normalization, smart reframing
(it center-crops), Approve/Regenerate buttons, performance tracking.
