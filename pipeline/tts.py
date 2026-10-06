"""Voiceover for typed text.

tts = "kokoro" (default): Kokoro-82M, an open neural TTS (Apache 2.0), run on CPU inside the
worker via ONNX. The model (~350MB) downloads into data/models/kokoro on first use.
tts = "say": the macOS host's `say` via tts_host.py - the worker runs in a Linux VM without it.
Either way the result is levelled to -16 LUFS, the music bed's loudness.
"""

import json
import threading
import urllib.error
import urllib.request
import wave
from pathlib import Path

import numpy as np

import config
import media

KOKORO_DIR = config.DATA_DIR / "models" / "kokoro"
KOKORO_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_FILES = ("kokoro-v1.0.onnx", "voices-v1.0.bin")
LANG = {"a": "en-us", "b": "en-gb"}  # voice prefix: af_/am_ American, bf_/bm_ British

_kokoro = None
_lock = threading.Lock()


def speak(text: str, out: Path, st: dict) -> None:
    raw = out.with_suffix(".raw.wav")
    if st["tts"].lower() == "say":
        _say(text, raw, st["voice"], st["voice_rate"])
    else:
        _kokoro_speak(text, raw, st["kokoro_voice"], st["kokoro_speed"])
    media.run(["ffmpeg", "-y", "-v", "error", "-i", str(raw), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
               "-ar", "48000", "-ac", "1", str(out)])
    raw.unlink()


def _get_kokoro():
    global _kokoro
    with _lock:
        if _kokoro is None:
            from kokoro_onnx import Kokoro  # heavy import; only when a voiceover is needed

            KOKORO_DIR.mkdir(parents=True, exist_ok=True)
            for name in KOKORO_FILES:
                dest = KOKORO_DIR / name
                if not dest.exists():
                    part = dest.with_suffix(dest.suffix + ".part")
                    urllib.request.urlretrieve(KOKORO_URL + name, part)
                    part.rename(dest)
            _kokoro = Kokoro(*(str(KOKORO_DIR / n) for n in KOKORO_FILES))
        return _kokoro


def _kokoro_speak(text: str, out: Path, voice: str, speed: float) -> None:
    k = _get_kokoro()
    if voice not in k.get_voices() or voice[0] not in LANG:
        english = ", ".join(v for v in k.get_voices() if v[0] in LANG)
        raise RuntimeError(f"unknown kokoro_voice {voice} - English voices: {english}")
    samples, rate = k.create(text, voice=voice, speed=speed, lang=LANG[voice[0]])
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())


def _say(text: str, out: Path, voice: str, rate: int) -> None:
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
            "- or send tts=kokoro / voiceover=off"
        ) from None
