"""Voiceover via the macOS host's `say` (tts_host.py) - the worker runs in a Linux VM without it."""

import json
import urllib.error
import urllib.request
from pathlib import Path

import config


def speak(text: str, out: Path, voice: str, rate: int) -> None:
    req = urllib.request.Request(
        f"{config.TTS_URL}/say", data=json.dumps({"text": text, "voice": voice, "rate": rate}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            out.write_bytes(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"voiceover failed: {e.read().decode(errors='replace')}") from None
    except OSError as e:
        raise RuntimeError(
            f"voiceover host not reachable at {config.TTS_URL} ({e}). On the Mac run: python3 tts_host.py "
            "- or send voiceover=off"
        ) from None
