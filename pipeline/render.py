"""Cut segments, reframe to 9:16, join, burn captions + quote, normalize loudness."""

import math
from pathlib import Path

import config
import media
import style
from plan import Segment

W, H, FPS = 1080, 1920, 30


def _ass() -> str:
    # Fonts dropped into content/fonts are usable by name in style.toml.
    return f"ass=captions.ass:fontsdir={config.CONTENT_DIR / 'fonts'}"


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


def build_ass(words: list[dict], quote: str, author: str | None, duration: float, st: dict,
              look: str = "video", quote_fit: bool = False) -> str:
    neon = look == "neon"
    # Captions sit bottom-aligned with their baseline at caption_y (fraction of the height).
    margin = round(H * (1 - st["neon_caption_y" if neon else "caption_y"]))
    if neon:
        # Black fill + thin light outline reads as hollow lettering; the glow is a blurred copy underneath.
        font, fill, highlight = st["neon_font"], "&H00000000&", style.ass_color(st["neon_highlight"])
        line = style.ass_color(st["neon_line"]).rstrip("&")
        styles = (
            f"Style: Caption,{font},{st['caption_size']},&H00000000,&H00000000,{line},&H00000000,"
            f"0,0,0,0,100,100,2,0,1,2,0,2,80,80,{margin},1\n"
            f"Style: Quote,{font},{st['quote_size']},&H00000000,&H00000000,{line},&H00000000,"
            "0,0,0,0,100,100,1,0,1,2,0,8,90,90,240,1"
        )
        g = st["glow"]
        glow = f"{{\\1a&HFF&\\3c{style.ass_color(st['neon_glow'])}\\bord{2 + g // 2}\\blur{g}}}" if g > 0 else ""
    else:
        font, fill, highlight, glow = st["font"], "&H00FFFFFF&", style.ass_color(st["highlight"]), ""
        styles = (
            f"Style: Caption,{font},{st['caption_size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
            f"-1,0,0,0,100,100,0,0,1,7,2,2,80,80,{margin},1\n"
            f"Style: Quote,{font},{st['quote_size']},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
            "-1,0,0,0,100,100,0,0,1,5,2,8,90,90,240,1"
        )
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{styles}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []

    def event(layer: int, start: float, end: float, name: str, text: str) -> None:
        # Separate layers per glow/text pair: libass shifts overlapping events on the same layer.
        if glow:
            events.append(f"Dialogue: {layer * 2},{_ts(start)},{_ts(end)},{name},,0,0,0,,{glow}{text}")
        events.append(f"Dialogue: {layer * 2 + 1},{_ts(start)},{_ts(end)},{name},,0,0,0,,{text}")

    quote_text = _esc(quote) + (f"\\N\\N- {_esc(author)}" if author else "")
    fit = ""
    if quote_fit:  # one line: no wrapping, font shrunk so ~0.6em per character fits the width
        size = min(st["quote_size"], int((W - 180) / (0.6 * max(1, len(quote)))))
        fit = f"\\q2\\fs{size}"
    event(1, 0, duration, "Quote", f"{{\\fad(300,300){fit}}}{quote_text}")
    # Blackletter capitals are unreadable, so the neon look keeps the words as typed.
    case = (lambda w: w) if neon else str.upper

    for chunk in _chunks(words, max_words=max(1, st["words_per_line"])):
        for i, w in enumerate(chunk):
            start = w["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else w["end"]
            text = " ".join(
                (f"{{\\c{highlight}}}{_esc(case(c['w']))}{{\\c{fill}}}" if j == i else _esc(case(c["w"])))
                for j, c in enumerate(chunk)
            )
            event(0, start, end, "Caption", text)
    return header + "\n".join(events) + "\n"


# Fractal level 0-7: opacity of a zooming Mandelbrot layer screen-blended over the footage.
# 5+ adds mirror symmetry, 7 adds hue cycling. Captions are burned in after, so stay crisp.
FRACTAL_OPACITY = [0.0, 0.08, 0.15, 0.22, 0.30, 0.38, 0.46, 0.55]
MIRROR = f"crop={W // 2}:{H}:0:0,split[l][r];[r]hflip[rf];[l][rf]hstack"  # left half + its mirror image


UPSCALE = f"scale={W}:{H},setsar=1"


def _mandelbrot(seconds: float, zoom_start: float, zoom_end: float, detail: int, inner: str = "mincol") -> str:
    # Half-res (cheap on CPU) - callers add UPSCALE; drifts along the seahorse valley. end_pts counts frames.
    return (
        f"mandelbrot=size={W // 2}x{H // 2}:rate={FPS}:maxiter={detail}:inner={inner}"
        f":start_scale={zoom_start}:end_scale={zoom_end}:end_pts={max(seconds, 1) * FPS:.0f}"
    )


def _gradient_map(colors: list[str]) -> str:
    """Recolour by brightness: dark -> bright mapped through the palette."""
    stops = [style.rgb(c) for c in colors]
    n = len(stops) - 1

    def channel(c: int) -> str:
        expr = str(stops[-1][c])
        for i in reversed(range(n)):
            a, b = stops[i][c], stops[i + 1][c]
            lo, hi = 255 * i / n, 255 * (i + 1) / n
            expr = f"if(lt(val,{hi:.1f}),{a}+({b - a})*(val-{lo:.1f})/{hi - lo:.1f},{expr})"
        return f"'{expr}'"

    return f"format=gray,format=gbrp,lutrgb=r={channel(0)}:g={channel(1)}:b={channel(2)}"


def _hook(src: str, dst: str, st: dict, duration: float) -> str:
    """Attention grab on the first moments: glitch and/or shake. On footage it runs before the captions
    so they stay crisp; on text clips after, so the hook text itself glitches."""
    mode, a = st["hook"].lower(), st["hook_strength"]
    d = round(min(st["hook_s"], duration / 3), 2)
    if mode == "off" or d <= 0 or a <= 0:
        return f"[{src}]null[{dst}]"
    fx = []
    if mode in ("shake", "both"):
        amp = round(40 * a)
        pad = amp + 8  # oversize by the max offset so the crop never leaves the frame
        decay = f"max(0,1-t/{d})"
        fx.append(
            f"scale={W + 2 * pad}:{H + 2 * pad},crop={W}:{H}"
            f":x='{pad}+{amp}*sin(97*t)*cos(41*t)*{decay}':y='{pad}+{amp}*cos(83*t)*sin(53*t)*{decay}'"
        )
    if mode in ("glitch", "both"):
        # Random-looking 48px bands slide sideways, channels split, grain, one negative frame.
        shift = f"{round(40 * a)}*gt(sin(floor(Y/48)*12.9898+N*78.233),0.6)*sin(N*1.7+floor(Y/48))"
        fx += [
            "format=gbrp",
            f"geq=r='r(X+{shift},Y)':g='g(X+0.5*{shift},Y)':b='b(X-{shift},Y)'",
            f"rgbashift=rh={-round(16 * a)}:bh={round(16 * a)}:enable='lt(mod(n,4),3)'",
            f"noise=alls={round(20 * a)}:allf=t",
            "negate=enable='eq(n,2)'",
        ]
    fx_chain = ",".join(fx)
    return (
        f"[{src}]split[hk_a][hk_b];"
        f"[hk_a]trim=0:{d},setpts=PTS-STARTPTS,{fx_chain},format=yuv420p,setsar=1[hk_h];"
        f"[hk_b]trim=start={d},setpts=PTS-STARTPTS,format=yuv420p,setsar=1[hk_r];"
        f"[hk_h][hk_r]concat=n=2:v=1:a=0[{dst}]"
    )


def video_filter(st: dict, duration: float) -> str:
    level = max(0, min(7, st["fractal"]))
    hook = _hook("0:v", "hooked", st, duration)
    if level == 0:
        return f"{hook};[hooked]{_ass()}[v]"
    base = "[hooked]"
    if level >= 5:
        base += MIRROR + ","
    if level >= 7:
        base += "hue=H=2*PI*t/8,"
    base += "format=gbrp[base]"
    fractal = _mandelbrot(duration, st["overlay_zoom_start"], st["overlay_zoom_end"], 256) + f",{UPSCALE},format=gbrp[fr]"
    blend = (
        f"[base][fr]blend=all_mode=screen:all_opacity={FRACTAL_OPACITY[level]}:shortest=1,"
        f"format=yuv420p,{_ass()}[v]"
    )
    return ";".join([hook, base, fractal, blend])


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


# --- text-only: hook card -> fractal zoom, no footage. look = neon | pixel -------

LEAD_S, TAIL_S = 0.3, 0.4  # pause before the first hook word / after the last


def timed_words(text: str, pace: float) -> list[dict]:
    """Fake word timings at a steady reading pace, in the transcript's shape."""
    return [
        {"w": w, "start": LEAD_S + i * pace, "end": LEAD_S + (i + 0.9) * pace}
        for i, w in enumerate(text.split())
    ]


def card_duration(words: list[dict]) -> float:
    return round((words[-1]["end"] if words else 0) + TAIL_S, 2)


def text_duration(card_s: float, st: dict) -> float:
    """Card + at least fractal_s of fractal. With the bassline and loop_align, rounded up to end on a
    whole loop (the note pattern padded to full 4-beat bars), so the clip loops cleanly on replay."""
    total = card_s + st["fractal_s"]
    if st["audio"].lower() == "bass" and st["loop_align"]:
        beats = math.ceil(len(st["bass_notes"].split()) / 4) * 4
        loop = beats * 60 / st["bpm"]
        total = math.ceil(total / loop - 1e-6) * loop
    return round(total, 2)


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


def _synth(freq: str, phase: str) -> str:
    """The one voice: sine + fifth + octave."""
    return f"sin(2*PI*{freq}*{phase})+0.4*sin(3*PI*{freq}*{phase})+0.2*sin(4*PI*{freq}*{phase})"


def _audio(st: dict, duration: float) -> str:
    """TikTok wants an audio track; a TikTok sound can still be added in-app."""
    mode = st["audio"].lower()
    if mode == "off":
        return f"anullsrc=r=48000:cl=stereo,atrim=duration={duration}[a]"
    if mode == "drone":  # held note with a slow swell
        expr = f"0.3*(0.8+0.2*sin(2*PI*0.25*t))*({_synth(st['drone_hz'], 't')})"
    else:  # bass: one plucked note per beat, looping through bass_notes
        beat = 60 / st["bpm"]
        notes = [style.note_hz(n) for n in st["bass_notes"].split()]
        freq = str(notes[-1])
        for i, hz in reversed(list(enumerate(notes[:-1]))):
            freq = f"if(eq(mod(floor(t/{beat}),{len(notes)}),{i}),{hz},{freq})"
        # st/ld: 0 = time since the beat, 1 = note frequency. Restarting the phase per note avoids clicks.
        expr = (
            f"st(0,mod(t,{beat}));st(1,{freq});"
            f"0.3*(1-exp(-60*ld(0)))*exp(-3*ld(0)/{beat})*({_synth('ld(1)', 'ld(0)')})"
        )
    return (
        f"aevalsrc='{expr}':s=48000:c=stereo:d={duration},afade=t=in:d=0.05,"
        f"afade=t=out:st={max(0, duration - 0.8):.2f}:d=0.8,loudnorm=I=-14:TP=-1.5:LRA=11[a]"
    )


def render_text(work: Path, ass: str, out: Path, card_s: float, duration: float, st: dict) -> None:
    fractal_s = round(duration - card_s, 2)
    (work / "captions.ass").write_text(ass, encoding="utf-8")
    neon = st["look"] == "neon"
    fractal = _mandelbrot(fractal_s, st["zoom_start"], st["zoom_end"], st["detail"], "black" if neon else "mincol")
    fractal += f",trim=duration={fractal_s}"
    if neon:
        fractal += "," + _gradient_map(st["neon_palette"].split())
        if st["bloom"] > 0:  # soft glow: a blurred copy screened back on top
            fractal += f",split[fa][fb];[fb]gblur=sigma=10[fbb];[fa][fbb]blend=all_mode=screen:all_opacity={st['bloom']}"
    fractal += f",{UPSCALE}"
    if st["mirror"]:
        fractal += "," + MIRROR
    if st["hue_cycle_s"] > 0:
        fractal += f",hue=H=2*PI*t/{st['hue_cycle_s']}"
    fractal += ",format=yuv420p[fr]"
    card = f"color=c=black:s={W}x{H}:r={FPS}:d={card_s},format=yuv420p[card]" if neon else _card(card_s, st)
    graph = ";".join([
        # Hook after the captions here: on a plain card, the text is the only thing to glitch.
        card, fractal, f"[card][fr]concat=n=2:v=1:a=0,{_ass()}[cat]", _hook("cat", "v", st, duration),
        _audio(st, duration),
    ])
    media.run([
        "ffmpeg", "-y", "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
        str(out.resolve()),
    ], cwd=work)
