"""Cut segments, reframe to 9:16, join, burn captions + quote, normalize loudness."""

from pathlib import Path

import media
import style
from plan import Segment

W, H, FPS = 1080, 1920, 30


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


def build_ass(words: list[dict], quote: str, author: str | None, duration: float, st: dict) -> str:
    font, highlight = st["font"], style.ass_color(st["highlight"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{font},{st["caption_size"]},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,80,80,560,1
Style: Quote,{font},{st["quote_size"]},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,5,2,8,90,90,240,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    quote_text = _esc(quote) + (f"\\N\\N- {_esc(author)}" if author else "")
    events.append(f"Dialogue: 1,{_ts(0)},{_ts(duration)},Quote,,0,0,0,,{{\\fad(300,300)}}{quote_text}")

    for chunk in _chunks(words, max_words=max(1, st["words_per_line"])):
        for i, w in enumerate(chunk):
            start = w["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else w["end"]
            text = " ".join(
                (f"{{\\c{highlight}}}{_esc(c['w'].upper())}{{\\c&H00FFFFFF&}}" if j == i else _esc(c["w"].upper()))
                for j, c in enumerate(chunk)
            )
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Caption,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"


# Fractal level 0-7: opacity of a zooming Mandelbrot layer screen-blended over the footage.
# 5+ adds mirror symmetry, 7 adds hue cycling. Captions are burned in after, so stay crisp.
FRACTAL_OPACITY = [0.0, 0.08, 0.15, 0.22, 0.30, 0.38, 0.46, 0.55]
MIRROR = f"crop={W // 2}:{H}:0:0,split[l][r];[r]hflip[rf];[l][rf]hstack"  # left half + its mirror image


def _mandelbrot(seconds: float, zoom_start: float, zoom_end: float, detail: int) -> str:
    # Low-res (cheap on CPU) and scaled up; drifts along the seahorse valley. end_pts counts frames.
    return (
        f"mandelbrot=size={W // 2}x{H // 2}:rate={FPS}:maxiter={detail}"
        f":start_scale={zoom_start}:end_scale={zoom_end}:end_pts={max(seconds, 1) * FPS:.0f}"
        f",scale={W}:{H},setsar=1"
    )


def video_filter(st: dict, duration: float) -> str:
    level = max(0, min(7, st["fractal"]))
    if level == 0:
        return "[0:v]ass=captions.ass[v]"
    base = "[0:v]"
    if level >= 5:
        base += MIRROR + ","
    if level >= 7:
        base += "hue=H=2*PI*t/8,"
    base += "format=gbrp[base]"
    fractal = _mandelbrot(duration, st["overlay_zoom_start"], st["overlay_zoom_end"], 256) + ",format=gbrp[fr]"
    blend = (
        f"[base][fr]blend=all_mode=screen:all_opacity={FRACTAL_OPACITY[level]}:shortest=1,"
        "format=yuv420p,ass=captions.ass[v]"
    )
    return ";".join([base, fractal, blend])


def finalize(joined: Path, ass: str, out: Path, st: dict, duration: float = 0) -> None:
    work = joined.parent
    (work / "captions.ass").write_text(ass, encoding="utf-8")
    media.run([
        "ffmpeg", "-y", "-i", joined.name,
        "-filter_complex", video_filter(st, duration),
        "-map", "[v]", "-map", "0:a",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
        str(out.resolve()),
    ], cwd=work)


# --- text-only: hook card -> fractal zoom, no footage --------------------------

LEAD_S, TAIL_S = 0.3, 0.4  # pause before the first hook word / after the last


def timed_words(text: str, pace: float) -> list[dict]:
    """Fake word timings at a steady reading pace, in the transcript's shape."""
    return [
        {"w": w, "start": LEAD_S + i * pace, "end": LEAD_S + (i + 0.9) * pace}
        for i, w in enumerate(text.split())
    ]


def card_duration(words: list[dict]) -> float:
    return round((words[-1]["end"] if words else 0) + TAIL_S, 2)


def _card(d: float, st: dict) -> str:
    """Two-colour split with a rising gradient line, a checker strip and blinking pixels.
    Drawn at half resolution and upscaled with nearest-neighbour for the pixel look."""
    line = f"lte(abs(Y-(H*(1-1.1*T/{d})-0.27*X)),4)" if st["line"] else "0"
    checker = "gte(X,W*0.9)*between(Y,H*0.62,H*0.86)*mod(floor(X/6)+floor(Y/6),2)" if st["checker"] else "0"
    bits = (
        "lt(mod(T*4,2),1)*(between(X,W-14,W-9)*between(Y,4,9)+between(X,W-8,W-3)*between(Y,12,17))"
        if st["pixels"] else "0"
    )
    colors = [style.rgb(st[k]) for k in ("card_left", "card_right", "line_from", "line_to", "checker_color")]
    bits_rgb = (200, 200, 60)

    def channel(c: int) -> str:
        left, right, lfrom, lto, chk = (col[c] for col in colors)
        return (
            f"'if({line},{lfrom}+({lto - lfrom})*X/W,if({checker},{chk},"
            f"if({bits},{bits_rgb[c]},if(lt(X,W/2),{left},{right}))))'"
        )

    geq = f"geq=r={channel(0)}:g={channel(1)}:b={channel(2)}"
    return (
        f"color=c=black:s={W // 2}x{H // 2}:r={FPS}:d={d},format=gbrp,{geq},"
        f"scale={W}:{H}:flags=neighbor,setsar=1,format=yuv420p[card]"
    )


def render_text(work: Path, ass: str, out: Path, card_s: float, duration: float, st: dict) -> None:
    fractal_s = round(duration - card_s, 2)
    (work / "captions.ass").write_text(ass, encoding="utf-8")
    fractal = _mandelbrot(fractal_s, st["zoom_start"], st["zoom_end"], st["detail"]) + f",trim=duration={fractal_s}"
    if st["mirror"]:
        fractal += "," + MIRROR
    if st["hue_cycle_s"] > 0:
        fractal += f",hue=H=2*PI*t/{st['hue_cycle_s']}"
    fractal += ",format=yuv420p[fr]"
    if st["drone"]:
        # Low drone with a slow swell - TikTok wants an audio track; add a TikTok sound in-app if wanted.
        hz = st["drone_hz"]
        audio = (
            f"aevalsrc='0.3*sin(2*PI*{hz}*t)*(0.8+0.2*sin(2*PI*0.25*t))+0.12*sin(2*PI*{hz * 1.5}*t)"
            f"+0.06*sin(2*PI*{hz * 2}*t)':s=48000:c=stereo:d={duration},afade=t=in:d=0.5,"
            f"afade=t=out:st={max(0, duration - 0.8):.2f}:d=0.8,loudnorm=I=-14:TP=-1.5:LRA=11[a]"
        )
    else:
        audio = f"anullsrc=r=48000:cl=stereo,atrim=duration={duration}[a]"
    graph = ";".join([
        _card(card_s, st), fractal, "[card][fr]concat=n=2:v=1:a=0,ass=captions.ass[v]", audio,
    ])
    media.run([
        "ffmpeg", "-y", "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
        str(out.resolve()),
    ], cwd=work)
