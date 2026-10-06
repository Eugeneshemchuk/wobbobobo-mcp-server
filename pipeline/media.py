"""Thin wrappers around ffmpeg/ffprobe."""

import json
import subprocess
from pathlib import Path


def run(args: list[str], cwd: Path | None = None) -> None:
    proc = subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RuntimeError(f"{args[0]} failed ({proc.returncode}):\n{tail}")


def probe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    return {
        "duration": float(info["format"]["duration"]),
        "has_audio": any(s["codec_type"] == "audio" for s in info["streams"]),
    }



def decode_audio_16k(src: Path):
    """Mono 16kHz float32 samples - the input format Whisper expects."""
    import numpy as np

    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-f", "f32le", "-"],
        capture_output=True, check=True,
    )
    return np.frombuffer(proc.stdout, dtype=np.float32)
