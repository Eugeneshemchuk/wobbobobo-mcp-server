# Wobbo Bobo clip pipeline

Telegram -> transcribe -> plain-Python segment/quote pick -> FFmpeg render -> TikTok inbox.
README.md covers usage and setup, and LOG.md records the current state and open items. Read both before larger changes.

## Rules
- No LLM at runtime. Segments, quotes, captions and hashtags are all deterministic Python or come from `content/`.
- No database. A job is a folder under `data/jobs/<id>`, and each step skips if its output already exists, so `/retry` resumes from the failed step. Keep new steps idempotent the same way.
- `content/` (quotes, `style.toml`) is bind-mounted read-only, so edits apply to the next job without a rebuild. Python changes need `docker compose up -d --build`.
- `JOB_RETENTION_DAYS` (30) is promised in the privacy policy. Don't change retention without updating the policy.
- Every new `style.toml` key must also work as a per-job `key=value` override. Unknown flags are rejected before anything is queued.
- Per-job randomness (drift style/target, music) is seeded by the job id, so a retry reproduces the same output.

## Run / debug
- `docker compose up -d --build`, then `docker compose logs worker` for tracebacks
- `data/bot.log` has one line per event
- `python pipeline.py data/jobs/<id> [--publish]` runs one job by hand
- `python cleanup.py --dry-run`
- Runtime is colima (4 CPU, 6 GB). The worker uses about 1.35 GB while rendering.

## Git
- Work and commit on `dev`, the long-lived branch. Don't create feature branches or PRs unless asked, and commit only when asked.
- Don't `git switch` to a commit that lacks `pipeline/`. It recreates `content/`, and the worker's bind mount then sees an empty dir (FileNotFoundError on quotes.txt). Sync main with `git fetch origin main:main`. If it happens anyway, run `docker compose restart worker`.
- After notable work, add a dated entry to LOG.md.

## Style
- Use regular dashes (-), never em-dashes, in code, docs and messages.
