"""Cut segments, reframe to 9:16, join, burn captions + quote, normalize loudness."""

from pathlib import Path

import config
import media
from plan import Segment

W, H, FPS = 1080, 1920, 30
HIGHLIGHT = "&H0000E5FF&"  # ASS is &HAABBGGRR - this is yellow


def cut_segments(clip_paths: list[Path], segments: list[Segment], work: Path) -> Path:
    parts = []
    for i, s in enumerate(segments):
        src = clip_paths[s.clip]
        out = work / f"seg_{i:02d}.mp4"
        parts.append(out)
        if out.exists():
            continue
        vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={FPS}"
        args = ["ffmpeg", "-y", "-ss", f"{s.start:.3f}", "-t", f"{s.end - s.start:.3f}", "-i", str(src)]
        if media.probe(src)["has_audio"]:
            args += ["-map", "0:v:0", "-map", "0:a:0"]
        else:
            args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a", "-shortest"]
        args += [
            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ar", "48000", "-ac", "2", "-b:a", "192k", str(out),
        ]
        media.run(args)

    joined = work / "joined.mp4"
    (work / "concat.txt").write_text("".join(f"file '{p.name}'\n" for p in parts))
    media.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "concat.txt", "-c", "copy", joined.name], cwd=work)
    return joined


def remap_words(clips_words: list[list[dict]], segments: list[Segment]) -> list[dict]:
    """Move source-clip word timings onto the edited timeline."""
    out, offset = [], 0.0
    for s in segments:
        for w in clips_words[s.clip]:
            if w["end"] <= s.start or w["start"] >= s.end:
                continue
            out.append({
                "w": w["w"],
                "start": max(w["start"], s.start) - s.start + offset,
                "end": min(w["end"], s.end) - s.start + offset,
            })
        offset += s.end - s.start
    return out


def _ts(t: float) -> str:
    cs = max(0, round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _esc(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def _chunks(words: list[dict], max_words: int = 3, max_gap: float = 0.6) -> list[list[dict]]:
    chunks, cur = [], []
    for w in words:
        if cur and (len(cur) >= max_words or w["start"] - cur[-1]["end"] > max_gap):
            chunks.append(cur)
            cur = []
        cur.append(w)
        if w["w"].endswith((".", "?", "!", ",")):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[dict], quote: str, author: str | None, duration: float) -> str:
    font = config.CAPTION_FONT
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,80,80,560,1
Style: Quote,{font},62,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,8,90,90,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    quote_text = _esc(quote) + (f"\\N\\N- {_esc(author)}" if author else "")
    events.append(f"Dialogue: 1,{_ts(0)},{_ts(duration)},Quote,,0,0,0,,{{\\fad(300,300)}}{quote_text}")

    for chunk in _chunks(words):
        for i, w in enumerate(chunk):
            start = w["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else w["end"]
            text = " ".join(
                (f"{{\\c{HIGHLIGHT}}}{_esc(c['w'].upper())}{{\\c&H00FFFFFF&}}" if j == i else _esc(c["w"].upper()))
                for j, c in enumerate(chunk)
            )
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"


# Fractality 0-7: opacity of a zooming Mandelbrot layer screen-blended over the footage.
# 5+ adds mirror symmetry, 7 adds hue cycling. Captions are burned in after, so stay crisp.
FRACTAL_OPACITY = [0.0, 0.08, 0.15, 0.22, 0.30, 0.38, 0.46, 0.55]


def video_filter(fractality: int, duration: float) -> str:
    level = max(0, min(7, fractality))
    if level == 0:
        return "[0:v]ass=captions.ass[v]"
    base = "[0:v]"
    if level >= 5:
        # Left half + its mirror image: kaleidoscope-like symmetry.
        base += f"crop={W // 2}:{H}:0:0,split[l][r];[r]hflip[rf];[l][rf]hstack,"
    if level >= 7:
        base += "hue=H=2*PI*t/8,"
    base += "format=gbrp[base]"
    # Low-res fractal (cheap on CPU) scaled up; zooms into the seahorse valley over the clip.
    fractal = (
        f"mandelbrot=size={W // 2}x{H // 2}:rate={FPS}:maxiter=256:start_scale=3:end_scale=0.02"
        f":end_pts={max(duration, 1):.2f},scale={W}:{H},format=gbrp[fr]"
    )
    blend = (
        f"[base][fr]blend=all_mode=screen:all_opacity={FRACTAL_OPACITY[level]}:shortest=1,"
        "format=yuv420p,ass=captions.ass[v]"
    )
    return ";".join([base, fractal, blend])


def finalize(joined: Path, ass: str, out: Path, fractality: int = 0, duration: float = 0) -> None:
    work = joined.parent
    (work / "captions.ass").write_text(ass, encoding="utf-8")
    media.run([
        "ffmpeg", "-y", "-i", joined.name,
        "-filter_complex", video_filter(fractality, duration),
        "-map", "[v]", "-map", "0:a",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
        str(out.resolve()),
    ], cwd=work)
