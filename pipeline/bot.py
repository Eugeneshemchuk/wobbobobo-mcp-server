"""Telegram front door. Send one video (or an album of videos) with an optional
caption = the quote. The bot renders it and drops it into your TikTok inbox.

Commands:
  /retry <job_id>   resume a failed job from the step that failed
  /jobs             last 10 jobs and their status
"""

import asyncio
import logging
import re
import time
import uuid
from pathlib import Path

from telegram import Message, Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import cleanup
import config
import pipeline

log = logging.getLogger("bot")
ALBUM_WAIT_S = 3.0
FRACTAL_TAG = re.compile(r"\bfractal\s*=\s*([0-7])\b", re.IGNORECASE)

queue: asyncio.Queue[Path] = asyncio.Queue()
_albums: dict[str, list[Message]] = {}
_tasks: list[asyncio.Task] = []


def _new_job_dir() -> Path:
    job = config.JOBS_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    job.mkdir(parents=True)
    return job


async def _ingest(messages: list[Message], context: ContextTypes.DEFAULT_TYPE) -> None:
    job = _new_job_dir()
    for i, m in enumerate(messages):
        media = m.video or m.document
        tg_file = await context.bot.get_file(media.file_id)
        await tg_file.download_to_drive(job / f"input_{i:02d}.mp4")
    caption = next((m.caption for m in messages if m.caption), "") or ""
    # Optional per-video setting in the caption, e.g. "fractal=5"; the rest is the quote.
    if m := FRACTAL_TAG.search(caption):
        (job / "fractality.txt").write_text(m.group(1))
        caption = FRACTAL_TAG.sub("", caption)
    if caption.strip():
        (job / "quote.txt").write_text(caption.strip())
    (job / "status").write_text("ingested")
    await messages[0].reply_text(f"Job {job.name}: {len(messages)} clip(s), queued.")
    await queue.put(job)


async def on_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    group = msg.media_group_id
    if not group:
        await _ingest([msg], context)
        return
    # Albums arrive as separate updates sharing media_group_id - collect, then ingest once.
    if group in _albums:
        _albums[group].append(msg)
        return
    _albums[group] = [msg]
    await asyncio.sleep(ALBUM_WAIT_S)
    msgs = sorted(_albums.pop(group), key=lambda m: m.message_id)
    await _ingest(msgs, context)


async def on_retry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.effective_message.reply_text("Usage: /retry <job_id>")
        return
    job = config.JOBS_DIR / context.args[0]
    if not job.is_dir():
        await update.effective_message.reply_text("No such job.")
        return
    await queue.put(job)
    await update.effective_message.reply_text(f"Job {job.name} re-queued.")


async def on_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    jobs = sorted(config.JOBS_DIR.glob("*"), reverse=True)[:10]
    lines = [f"{j.name}  {(j / 'status').read_text() if (j / 'status').exists() else '?'}" for j in jobs]
    await update.effective_message.reply_text("\n".join(lines) or "No jobs yet.")


async def worker(app: Application) -> None:
    """One job at a time - ffmpeg already uses every core."""
    loop = asyncio.get_running_loop()
    chat = config.TELEGRAM_ALLOWED_CHAT_ID

    while True:
        job = await queue.get()

        def notify(text: str, job=job) -> None:
            asyncio.run_coroutine_threadsafe(app.bot.send_message(chat, f"[{job.name}] {text}"), loop)

        try:
            final = await asyncio.to_thread(pipeline.process, job, notify)
            plan = pipeline.Plan.model_validate_json((job / "plan.json").read_text())
            tags = " ".join(f"#{t.lstrip('#')}" for t in plan.hashtags)
            with final.open("rb") as f:
                await app.bot.send_video(
                    chat, f, supports_streaming=True,
                    caption=f"Caption to paste in TikTok:\n\n{plan.tiktok_caption}\n{tags}"[:1024],
                )
            if not pipeline.tiktok.connected():
                await app.bot.send_message(chat, f"[{job.name}] TikTok not connected yet - skipped the upload. "
                                                 f"After connecting, /retry {job.name} uploads it.")
                (job / "status").write_text("rendered")
                continue
            result = await asyncio.to_thread(pipeline.publish, job, notify)
            await app.bot.send_message(
                chat,
                f"[{job.name}] In your TikTok inbox ({result['status']}). "
                "Open TikTok -> inbox notification -> finish and post.",
            )
        except Exception as e:  # noqa: BLE001 - surface every failure to Telegram
            log.exception("job %s failed", job.name)
            (job / "status").write_text(f"failed: {type(e).__name__}")
            await app.bot.send_message(chat, f"[{job.name}] FAILED: {e}\n\nFix and /retry {job.name}"[:4000])
        finally:
            queue.task_done()


async def cleanup_loop() -> None:
    """Enforce the retention period: once at startup, then daily."""
    while True:
        try:
            await asyncio.to_thread(cleanup.prune)
        except Exception:  # noqa: BLE001 - never let cleanup kill the bot
            log.exception("cleanup failed")
        await asyncio.sleep(24 * 3600)


async def _post_init(app: Application) -> None:
    # Pick up jobs that were mid-flight when the process last stopped.
    for job in sorted(config.JOBS_DIR.glob("*")):
        status = (job / "status").read_text() if (job / "status").exists() else ""
        # Rendered jobs are not resumed: they would re-send a preview. Use /retry for those.
        if status in ("ingested", "transcribed", "planned"):
            await queue.put(job)
    # Plain asyncio tasks: run_polling keeps the loop alive; keep references so they aren't GC'd.
    _tasks.extend([asyncio.create_task(worker(app)), asyncio.create_task(cleanup_loop())])


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config.JOBS_DIR.mkdir(parents=True, exist_ok=True)

    builder = (
        Application.builder()
        .token(config.TELEGRAM_BOT_TOKEN)
        .concurrent_updates(True)  # album collection sleeps in a handler; don't block the rest
        .read_timeout(300)
        .write_timeout(300)
        .post_init(_post_init)
    )
    if config.TELEGRAM_API_BASE:
        base = config.TELEGRAM_API_BASE.rstrip("/")
        builder = builder.base_url(f"{base}/bot").base_file_url(f"{base}/file/bot").local_mode(True)
    app = builder.build()

    only_me = filters.Chat(chat_id=config.TELEGRAM_ALLOWED_CHAT_ID)
    app.add_handler(MessageHandler(only_me & (filters.VIDEO | filters.Document.VIDEO), on_video))
    app.add_handler(CommandHandler("retry", on_retry, filters=only_me))
    app.add_handler(CommandHandler("jobs", on_jobs, filters=only_me))
    app.run_polling()


if __name__ == "__main__":
    main()
