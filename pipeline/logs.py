"""Logging: console (docker compose logs) + a compact, size-capped file at data/bot.log.

One line per event. Exceptions collapse to "Type: first ... last line @ file:line" instead of a
multi-line traceback. Job lines carry ids, kinds, flags and timings - never hook or quote text.
"""

import logging
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config

FORMAT, DATEFMT = "%(asctime)s %(levelname).1s %(name)s: %(message)s", "%m-%d %H:%M:%S"


class OneLine(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = super().format(logging.makeLogRecord({**record.__dict__, "exc_info": None, "exc_text": None}))
        if record.exc_info and record.exc_info[1] is not None:
            etype, err, tb = record.exc_info
            lines = str(err).strip().splitlines() or [""]
            text = lines[0] if len(lines) == 1 else f"{lines[0]} ... {lines[-1]}"  # ffmpeg: cause is last
            where = traceback.extract_tb(tb)[-1] if tb else None
            line += f" | {etype.__name__}: {text}" + (f" @ {Path(where.filename).name}:{where.lineno}" if where else "")
        return line.replace("\n", " ")


def setup() -> None:
    config.LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(FORMAT, DATEFMT))  # full tracebacks stay in docker logs
    file = RotatingFileHandler(config.LOG_FILE, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    file.setFormatter(OneLine(FORMAT, DATEFMT))
    logging.basicConfig(level=logging.INFO, handlers=[console, file])
    # httpx logs every request URL at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Per-request noise; warnings still come through.
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("telegram.ext").setLevel(logging.WARNING)
