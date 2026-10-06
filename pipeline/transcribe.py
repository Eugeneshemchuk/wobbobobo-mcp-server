"""Local speech-to-text with faster-whisper (CPU, int8). No API key, audio never leaves the box."""

import os
from pathlib import Path

from faster_whisper import WhisperModel

import config
import media

_model: WhisperModel | None = None


def _get_model() -> WhisperModel:
    global _model
    if _model is None:
        # First use downloads the model into data/models (persisted across restarts).
        _model = WhisperModel(
            config.WHISPER_MODEL,
            device="cpu",
            compute_type="int8",
            cpu_threads=os.cpu_count() or 4,
            download_root=str(config.DATA_DIR / "models"),
        )
    return _model


def transcribe(src: Path, work: Path) -> list[dict]:
    """Word-level transcript of one clip: [{"w": str, "start": float, "end": float}]."""
    if not media.probe(src)["has_audio"]:
        return []
    # Decode with ffmpeg ourselves: phone codecs/containers vary, and it avoids faster-whisper's PyAV path.
    segments, _info = _get_model().transcribe(
        media.decode_audio_16k(src),
        language=config.WHISPER_LANGUAGE,
        word_timestamps=True,
        vad_filter=True,  # skip silence - faster, and fewer hallucinated words in dead air
    )
    return [
        {"w": w.word.strip(), "start": round(w.start, 3), "end": round(w.end, 3)}
        for seg in segments
        for w in (seg.words or [])
        if w.word.strip()
    ]
