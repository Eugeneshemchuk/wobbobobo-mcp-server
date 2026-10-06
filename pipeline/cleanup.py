"""Delete user media after JOB_RETENTION_DAYS, as promised in the privacy policy.

Removes:
- job folders (inputs, transcript, plan, intermediates, final clip) whose last activity is older
  than the retention period
- files the local Telegram Bot API server keeps from downloads (TELEGRAM_FILES_DIR)

Runs daily from the bot; manual run: python cleanup.py [--dry-run]
"""

import logging
import shutil
import sys
import time
from pathlib import Path

import config

log = logging.getLogger("cleanup")


def _last_activity(job: Path) -> float:
    return max((p.stat().st_mtime for p in job.rglob("*")), default=job.stat().st_mtime)


def prune(dry_run: bool = False) -> list[Path]:
    cutoff = time.time() - config.JOB_RETENTION_DAYS * 86400
    removed: list[Path] = []

    if config.JOBS_DIR.exists():
        for job in config.JOBS_DIR.iterdir():
            if job.is_dir() and _last_activity(job) < cutoff:
                removed.append(job)
                if not dry_run:
                    shutil.rmtree(job)

    if config.TELEGRAM_FILES_DIR and config.TELEGRAM_FILES_DIR.exists():
        for f in config.TELEGRAM_FILES_DIR.rglob("*"):
            # Only media the server downloaded; never its database or other state.
            if f.is_file() and f.parent.name in ("videos", "documents") and f.stat().st_mtime < cutoff:
                removed.append(f)
                if not dry_run:
                    f.unlink()

    for p in removed:
        log.info("%s %s", "would remove" if dry_run else "removed", p)
    return removed


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    n = len(prune(dry_run="--dry-run" in sys.argv))
    print(f"{n} item(s) {'would be ' if '--dry-run' in sys.argv else ''}removed")
