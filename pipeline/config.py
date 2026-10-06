import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.environ.get("DATA_DIR", "./data")).resolve()
JOBS_DIR = DATA_DIR / "jobs"
TOKENS_FILE = DATA_DIR / "tiktok_tokens.json"
QUOTES_USED_FILE = DATA_DIR / "quotes_used.log"

# Pre-written quotes, captions, hashtags - edit these files, no LLM at runtime.
CONTENT_DIR = Path(os.environ.get("CONTENT_DIR", Path(__file__).parent / "content")).resolve()
QUOTE_COOLDOWN_DAYS = int(os.environ.get("QUOTE_COOLDOWN_DAYS") or 30)

# Privacy policy promise: user media is deleted this many days after a job's last activity.
JOB_RETENTION_DAYS = int(os.environ.get("JOB_RETENTION_DAYS") or 30)
# Local Bot API server keeps its own copy of downloaded files here (pruned on the same schedule).
TELEGRAM_FILES_DIR = Path(os.environ["TELEGRAM_FILES_DIR"]) if os.environ.get("TELEGRAM_FILES_DIR") else None

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Only this chat can use the bot. Get it from @userinfobot.
TELEGRAM_ALLOWED_CHAT_ID = int(os.environ.get("TELEGRAM_ALLOWED_CHAT_ID") or 0)
# Set to the local Bot API server (e.g. http://telegram-bot-api:8081) to lift
# the 20MB download limit. Empty = cloud Bot API.
TELEGRAM_API_BASE = os.environ.get("TELEGRAM_API_BASE", "")

# Local faster-whisper: tiny | base | small | medium | large-v3. small = good CPU balance.
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
# Fixing the language skips detection and avoids misdetection on short clips. Empty = auto.
WHISPER_LANGUAGE = os.environ.get("WHISPER_LANGUAGE") or None

TIKTOK_CLIENT_KEY = os.environ.get("TIKTOK_CLIENT_KEY", "")
TIKTOK_CLIENT_SECRET = os.environ.get("TIKTOK_CLIENT_SECRET", "")
TIKTOK_REDIRECT_URI = os.environ.get("TIKTOK_REDIRECT_URI", "")

TARGET_MAX_S = float(os.environ.get("TARGET_MAX_S", "45"))
# Style and effects (fonts, colours, fractal, drone...) live in content/style.toml.
