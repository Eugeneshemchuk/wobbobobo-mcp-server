"""Fractal Drift (eugeneshemchuk.github.io/Portfolio/fractal) as a video background: a Mandelbrot
dive into a boundary "river" with slow spin, smooth colouring and the site's styles. A numpy port
of the page's shader, one process per frame, piped to ffmpeg. float64 is good to ~1e12x zoom.
"""

import colorsys
import math
import os
import random
import shutil
import subprocess
import multiprocessing
from pathlib import Path

import numpy as np

TARGETS = {
    # The page's three. Seahorse valley is a channel between two bulbs, so these open with a lot of
    # the set's black inside on a portrait frame - pick them by name.
    "seahorse": (-0.743643887037151, 0.131825904205330),
    "elephant": (0.2869318688950451, 0.014286693904085),
    "bulb": (-1.25066, 0.02012),
    # Checked to be mostly detail from the first frame and to stay detailed down to 100000x.
    "seahorse spiral": (-0.77568377, 0.13646737),
    "tendrils": (-0.7746806106269039, -0.1374168856037867),
    "classic spiral": (-0.761574, -0.0847596),
    "top spiral": (-0.16070135, 1.0375665),
    "elephant spirals": (0.42884, -0.231345),
    "needle spiral": (-1.315180982097868, 0.073481649996795),
    "star": (-0.5577, 0.6355),
}
RANDOM_TARGETS = ["seahorse spiral", "tendrils", "classic spiral", "top spiral", "elephant spirals",
                  "needle spiral", "star"]
OVERVIEW = (-0.6, 0.0)
# Zoom pace at speed 1: e^0.52 = 1.68x per second, ~500x over 12s - the pace of the first drift clips.
ZOOM_RATE = 0.52
GLIDE_MAX = (0.18, 0.3)  # max starting offset of the target from the centre, in frame heights
GLIDE = 0.9  # per second: how fast the target slides from its overview position to the centre
WARP_FADE = (20, 2000)  # zoom range over which warp eases off, so deep views show the true set
BG = np.array([0.043, 0.047, 0.059])

# The site's styles/*.json. gradient=None is the site's own indigo/blue/cyan palette (Midnight).
STYLES = {
    "classic": dict(magnify=2, smooth=1, spin=0.015, cycle=0, density=1, warp=0, warp_speed=0.15, bright=True,
                    gradient=[(0, "#000764"), (0.16, "#206bcb"), (0.42, "#edffff"), (0.6425, "#ffaa00"),
                              (0.8575, "#000200")]),
    "midnight": dict(magnify=2, smooth=1, spin=0.015, cycle=0, density=1, warp=0, warp_speed=0.15, bright=False,
                     gradient=None),
    "ocean": dict(magnify=2.5, smooth=1, spin=0.008, cycle=0.03, density=1.3, warp=0.2, warp_speed=0.12, bright=True,
                  gradient=[(0, "#010b1f"), (0.25, "#04395e"), (0.5, "#0f9b9b"), (0.7, "#bff7ee"), (0.85, "#2a7f9e")]),
    "ember": dict(magnify=2, smooth=1, spin=-0.01, cycle=0.08, density=0.8, warp=0.15, warp_speed=0.2, bright=True,
                  gradient=[(0, "#080101"), (0.3, "#6a0d0d"), (0.55, "#e2471b"), (0.75, "#ffc94a"),
                            (0.88, "#fff6dc")]),
    "neon": dict(magnify=1.5, smooth=1, spin=-0.03, cycle=0, density=1.8, warp=0.3, warp_speed=0.3, bright=True,
                 gradient=[(0, "#07060d"), (0.3, "#ff2fd0"), (0.45, "#07060d"), (0.75, "#2ff3ff"), (0.9, "#07060d")]),
    "topo": dict(magnify=3, smooth=0, spin=0, cycle=0, density=0.7, warp=0, warp_speed=0.15, bright=True,
                 gradient=[(0, "#f2e8cf"), (0.2, "#a7c957"), (0.4, "#6a994e"), (0.6, "#386641"), (0.8, "#bc4749")]),
}


def _random_gradient(rng: random.Random) -> list:
    """A new cyclic palette: dark -> two hues from a colour-wheel scheme -> a pale highlight -> dark,
    like the page's hand-made styles."""
    h0 = rng.random()
    spread = {"analogous": (0.08, 0.16), "complementary": (0.5, 0.45), "triadic": (0.33, 0.67),
              "split": (0.42, 0.58)}[rng.choice(["analogous", "complementary", "triadic", "split"])]
    h1, h2 = (h0 + spread[0]) % 1, (h0 + spread[1]) % 1

    def hexc(h: float, light: float, sat: float) -> str:
        r, g, b = colorsys.hls_to_rgb(h, light, sat)
        return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"

    p1, p2, p3, p4 = rng.uniform(0.18, 0.3), rng.uniform(0.4, 0.5), rng.uniform(0.6, 0.7), rng.uniform(0.82, 0.9)
    # Lifted darkest stop: the outer region (low counts) shows colour instead of near-black.
    return [(0, hexc(h0, rng.uniform(0.1, 0.18), 0.7)), (p1, hexc(h0, rng.uniform(0.3, 0.45), 0.85)),
            (p2, hexc(h1, rng.uniform(0.5, 0.62), 0.9)), (p3, hexc(h2, rng.uniform(0.82, 0.92), 0.5)),
            (p4, hexc(h2, 0.05, 0.6))]


def pick(style_name: str, target_name: str, seed: str) -> tuple[dict, str, str]:
    """Style and target for a job. "random" draws from the job id - a preset or a freshly generated
    palette, with spin, density, zoom framing and colour drift varied too - so every clip differs and a
    retry reproduces the same one. Returns (style, target name, label for the log)."""
    rng = random.Random(f"drift:{seed}")
    target = rng.choice(RANDOM_TARGETS) if target_name == "random" else target_name
    if style_name != "random":
        return dict(STYLES[style_name]), target, f"{style_name} on {target}"
    if rng.random() < 0.4:
        name = rng.choice(list(STYLES))
        st = dict(STYLES[name])
    else:
        name = "generated"
        st = dict(STYLES["classic"], gradient=_random_gradient(rng))
    st.update(
        spin=rng.choice([-1, 1]) * rng.uniform(0.008, 0.03), density=rng.uniform(0.8, 1.6),
        cycle=rng.choice([0, 0, 0.03, 0.06]), magnify=rng.uniform(1.5, 2.5),
    )
    colours = " ".join(c for _, c in st["gradient"]) if st["gradient"] else "site palette"
    return st, target, f"{name} ({colours}) on {target}, spin {st['spin']:+.3f}, density {st['density']:.2f}"


def _hex(c: str) -> np.ndarray:
    return np.array([int(c[i:i + 2], 16) / 255 for i in (1, 3, 5)])


def _smoothstep(a, b, x):
    x = np.clip((x - a) / (b - a), 0, 1)
    return x * x * (3 - 2 * x)


def _gradient(stops, t: np.ndarray) -> np.ndarray:
    """Cyclic gradient: the last colour wraps back to the first."""
    t = t % 1.0
    pos = [p for p, _ in stops] + [1.0]
    cols = [_hex(c) for _, c in stops] + [_hex(stops[0][1])]
    out = np.zeros(t.shape + (3,))
    for i in range(len(stops)):
        m = (t >= pos[i]) & (t < pos[i + 1]) if i else (t < pos[1])
        k = _smoothstep(pos[i], pos[i + 1], t[m])[:, None]
        out[m] = cols[i] * (1 - k) + cols[i + 1] * k
    return out


def _site_palette(t: np.ndarray) -> np.ndarray:
    a, b, c = np.array([0.36, 0.31, 0.91]), np.array([0.37, 0.69, 1.0]), np.array([0.34, 0.83, 0.87])
    s = (0.5 + 0.5 * np.sin(t))[..., None]
    h = (0.5 + 0.5 * np.sin(t * 0.37 + 1.3))[..., None]
    return (a * (1 - s) + b * s) * (1 - h * h) + c * h * h


LUT = 2048
_luts: dict = {}


def _gradient_lut(st: dict) -> np.ndarray:
    """The cyclic gradient sampled once; per-pixel lookups beat evaluating the stops every frame."""
    key = tuple(st["gradient"])
    if key not in _luts:
        _luts[key] = _gradient(st["gradient"], (np.arange(LUT) + 0.5) / LUT).astype(np.float32)
    return _luts[key]


def _palette_avg(st: dict) -> np.ndarray:
    if st["gradient"]:
        return _gradient(st["gradient"], (np.arange(64) + 0.5) / 64).mean(axis=0)
    return _site_palette(np.linspace(0, 200, 400)).mean(axis=0)


def _view(sec: float, total: float, st: dict, target: tuple, zoom_end: float) -> dict:
    """Camera at time sec, as the page's frame(): exponential zoom, glide to the centre, spin, warp."""
    zoom = math.exp(math.log(zoom_end) * sec / total) * st["base"]
    sec *= st["speed"]  # the rest of the motion runs on scaled time too
    glide = math.exp(-GLIDE * sec)
    f = (math.log(zoom) - math.log(WARP_FADE[0])) / math.log(WARP_FADE[1] / WARP_FADE[0])
    f = min(max(f, 0), 1)
    warp = st["warp"] * (1 - f * f * (3 - 2 * f))
    return dict(
        zoom=zoom,
        # Clamped so the target starts on screen: on a portrait frame the page's full offset puts
        # elephant/bulb 0.7 heights to the side and the opening is the black inside of the set.
        sx=max(-GLIDE_MAX[0], min(GLIDE_MAX[0], (target[0] - OVERVIEW[0]) / 2.6 * st["magnify"])) * glide,
        sy=max(-GLIDE_MAX[1], min(GLIDE_MAX[1], (target[1] - OVERVIEW[1]) / 2.6 * st["magnify"])) * glide,
        angle=st["spin"] * sec,
        max_iter=_max_iter(zoom),
        z0=complex(warp * math.cos(st["warp_speed"] * sec), warp * math.sin(st["warp_speed"] * sec)),
    )


def _max_iter(zoom: float) -> int:
    return int(min(140 + 55 * math.log2(zoom), 1400))


def _offsets(w: int, h: int, v: dict, xs=None, ys=None) -> np.ndarray:
    """Each pixel's offset from the target in the complex plane (the shader's dc)."""
    if xs is None:
        ys, xs = np.mgrid[h - 1:-1:-1, 0:w]  # flip: GL's y points up
    dx = (xs + 0.5 - 0.5 * w) / h - v["sx"]
    dy = (ys + 0.5 - 0.5 * h) / h - v["sy"]
    cs, sn = math.cos(v["angle"]), math.sin(v["angle"])
    return ((cs * dx - sn * dy) + 1j * (sn * dx + cs * dy)) * (2.6 / v["zoom"])


def _iterate(c: np.ndarray, z0: complex, max_iter: int, zoom: float) -> np.ndarray:
    """Smooth escape count per point (float32), -1 where the point never escaped."""
    n = np.zeros(c.shape)
    zfin = np.zeros(c.shape, dtype=np.complex128)
    esc = np.zeros(c.shape, dtype=bool)
    todo = np.ones(c.shape, dtype=bool)
    if z0 == 0:  # main cardioid and period-2 bulb never escape: skip their max_iter run (unwarped set only)
        x, y = c.real, c.imag
        q = (x - 0.25) ** 2 + y * y
        todo = ~((q * (q + x - 0.25) <= 0.25 * y * y) | ((x + 1) ** 2 + y * y <= 1 / 16))

    # float32 like the page's shader while it has the precision (it switches at ~6000x), float64 deeper.
    # Escaped points are parked at z = c = 0 (a fixed point) and the arrays compacted now and then,
    # so the loop does no per-iteration reallocation.
    ctype, rtype = (np.complex64, np.float32) if zoom < 3000 else (np.complex128, np.float64)
    idx = np.flatnonzero(todo)
    cc = c[idx].astype(ctype)
    zc = np.full(idx.size, z0, dtype=ctype)
    alive = np.ones(idx.size, dtype=bool)
    r2, tmp, out = np.empty(idx.size, rtype), np.empty(idx.size, rtype), np.empty(idx.size, bool)
    parked = 0
    for i in range(max_iter):
        np.multiply(zc, zc, out=zc)
        zc += cc
        np.square(zc.real, out=r2)
        r2 += np.square(zc.imag, out=tmp)
        np.greater(r2, 256, out=out)
        if not out.any():
            continue
        hit = np.flatnonzero(out)
        esc[idx[hit]], n[idx[hit]], zfin[idx[hit]] = True, i, zc[hit]
        zc[hit], cc[hit], alive[hit] = 0, 0, False
        parked += hit.size
        if parked > idx.size // 4:
            keep = np.flatnonzero(alive)
            if not keep.size:
                break
            idx, zc, cc = idx[keep], zc[keep], cc[keep]
            alive = np.ones(idx.size, dtype=bool)
            r2, tmp, out = np.empty(idx.size, rtype), np.empty(idx.size, rtype), np.empty(idx.size, bool)
            parked = 0

    r2 = np.maximum(np.abs(zfin) ** 2, 1.0001)
    return np.where(esc, np.maximum(n - np.log2(np.log2(r2)) + 4, 0), -1).astype(np.float32)


def _colour(sm: np.ndarray, esc: np.ndarray, w: int, h: int, st: dict, sec: float, max_iter: int) -> bytes:
    """The shader's colouring: sqrt-spaced palette, band smoothing, dark glow floor unless bright."""
    sm = np.where(esc, sm, 1.0)
    phase = st["cycle"] * sec * st["speed"]
    sn2 = (1 - st["smooth"]) * np.floor(sm) + st["smooth"] * sm
    tb = np.sqrt(sn2) * 1.1 * st["density"] + phase
    if st["gradient"]:
        pc, freq = _gradient_lut(st)[((tb * 0.15) % 1.0 * LUT).astype(np.int32) % LUT], 0.15
    else:
        pc, freq = _site_palette(tb), 1 / (2 * math.pi)

    # Band smoothing (the shader's fwidth): bands narrower than ~2px only shimmer, so fade to the average.
    t2 = (np.sqrt(sm) * 1.1 * st["density"] * freq).reshape(h, w)
    fw = np.abs(np.diff(t2, axis=1, append=t2[:, -1:])) + np.abs(np.diff(t2, axis=0, append=t2[-1:, :]))
    blend = _smoothstep(0.2, 0.7, fw.ravel())[:, None]
    pc = pc * (1 - blend) + _palette_avg(st) * blend

    if st["bright"]:
        col = pc
    else:
        lo = math.log(max(max_iter * 0.12 - 15, 1))
        g = np.clip((np.log(np.maximum(sn2, 1)) - lo) / (math.log(max_iter) - lo), 0, 1)[:, None]
        col = BG * (1 - g * np.sqrt(g)) + pc * g * np.sqrt(g)
    col = np.where(esc[:, None], col, BG)
    return (np.clip(col, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes()


def _frame(args) -> bytes:
    """quality=full: iterate every pixel of every frame, warp included - exact, slow."""
    w, h, sec, total, st, target, zoom_end = args
    v = _view(sec, total, st, target, zoom_end)
    sm = _iterate((complex(*target) + _offsets(w, h, v)).ravel(), v["z0"], v["max_iter"], v["zoom"])
    return _colour(sm, sm >= 0, w, h, st, sec, v["max_iter"])


# --- quality=fast: keyframes -----------------------------------------------------------------
# A constant-rate dive is the same picture getting bigger, so the set is only iterated on one
# keyframe per 2x of zoom, centred on the target at KEY_DENSITY x the frame's pixel density. Each
# frame resamples the escape counts of its keyframe (spin and glide are just the sampling grid) and
# is coloured on its own, so colour cycling still works. Warp bends the set over time, so it is off.
KEY_DENSITY = 2.0  # frames are 1-2x downsampled from their keyframe, never upsampled - no soft patches
# Each output pixel averages 2x2 sub-pixel lookups: fine filaments stop shimmering from frame to frame.
SUBPIXELS = ((-0.25, -0.25), (0.25, -0.25), (-0.25, 0.25), (0.25, 0.25))
BLEND_FROM = 0.75  # last quarter of each zoom octave fades into the next keyframe, so detail never pops
STRIP = 64  # keyframe rows per task

_keys: dict = {}  # per worker process: keyframe index -> memory-mapped escape counts


def _key_strip(args) -> np.ndarray:
    target, nx, ny, px, y0, y1, max_iter, zoom = args
    ys, xs = np.mgrid[y0:y1, 0:nx]
    c = complex(*target) + (xs + 0.5 - nx / 2) * px + 1j * (ny / 2 - (ys + 0.5)) * px
    return _iterate(c.ravel(), 0j, max_iter, zoom).reshape(y1 - y0, nx)


def _sample(key: np.ndarray, px: float, off: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear lookup weighted by escape, so the set's edge stays clean instead of smearing.
    Returns (escape-weighted sum, escape weight 0-1)."""
    ny, nx = key.shape
    u = off.real / px + nx / 2 - 0.5
    v = ny / 2 - off.imag / px - 0.5
    x0, y0 = np.floor(u).astype(np.int64), np.floor(v).astype(np.int64)
    fx, fy = u - x0, v - y0
    total = np.zeros(off.shape)
    weight = np.zeros(off.shape)
    for ox, oy, wt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        s = key[np.clip(y0 + oy, 0, ny - 1), np.clip(x0 + ox, 0, nx - 1)]
        e = s >= 0
        weight += wt * e
        total += wt * e * s
    return total, weight


def _key_frame(args) -> bytes:
    w, h, sec, total, st, target, zoom_end, key_dir, meta = args
    v = _view(sec, total, st, target, zoom_end)
    v["z0"] = 0j
    k, f = _key_pos(v["zoom"], st, meta)
    # Both keyframes hold the same function at different resolutions, so escape counts blend cleanly.
    mix = [(k, 1.0)]
    if f > BLEND_FROM and k + 1 < len(meta):
        a = float(_smoothstep(BLEND_FROM, 1.0, f))
        mix = [(k, 1 - a), (k + 1, a)]
    ys, xs = np.mgrid[h - 1:-1:-1, 0:w]
    sm_sum, esc_sum = 0.0, 0.0
    for dx, dy in SUBPIXELS:
        off = _offsets(w, h, v, xs + dx, ys + dy).ravel()
        for key, wt in mix:
            if key not in _keys:
                _keys[key] = np.load(key_dir / f"k{key}.npy", mmap_mode="r")
            t, e = _sample(_keys[key], meta[key]["px"], off)
            sm_sum, esc_sum = sm_sum + wt * t, esc_sum + wt * e
    esc_sum = esc_sum / len(SUBPIXELS)
    sm = sm_sum / len(SUBPIXELS) / np.maximum(esc_sum, 1e-6)
    return _colour(sm, esc_sum > 0.5, w, h, st, sec, v["max_iter"])


def _key_pos(zoom: float, st: dict, meta: list) -> tuple[int, float]:
    """Keyframe index for a zoom, and how far through its octave (0-1) the zoom is."""
    octave = max(math.log2(zoom / st["base"]), 0.0)
    k = min(int(octave + 1e-9), len(meta) - 1)
    return k, octave - k


def _keyframes(key_dir: Path, w: int, h: int, fps: int, seconds: float, st: dict, target: tuple,
               zoom_end: float, frames: int, pool) -> list[dict]:
    count = max(1, math.ceil(math.log2(zoom_end) - 1e-9))
    meta = [dict(ex=0.0, ey=0.0) for _ in range(count)]
    # Each keyframe covers every frame that samples it - the affine view puts the extremes at the corners.
    xs, ys = np.array([0, w - 1, 0, w - 1]), np.array([0, 0, h - 1, h - 1])
    for i in range(frames):
        v = _view(i / fps, seconds, st, target, zoom_end)
        k, f = _key_pos(v["zoom"], st, meta)
        corners = _offsets(w, h, v, xs, ys)
        for m in [meta[k]] + ([meta[k + 1]] if f > BLEND_FROM and k + 1 < len(meta) else []):
            m["ex"] = max(m["ex"], np.abs(corners.real).max() + 1 / (h * st["base"] * 2 ** k))
            m["ey"] = max(m["ey"], np.abs(corners.imag).max() + 1 / (h * st["base"] * 2 ** k))
    tasks = []
    for k, m in enumerate(meta):
        zoom = st["base"] * 2 ** k
        m["px"] = 2.6 / (zoom * h * KEY_DENSITY)
        m["nx"] = 2 * math.ceil(m["ex"] / m["px"]) + 4
        m["ny"] = 2 * math.ceil(m["ey"] / m["px"]) + 4
        # Detail for the deepest frame that uses it.
        tasks += [(target, m["nx"], m["ny"], m["px"], y, min(y + STRIP, m["ny"]), _max_iter(2 * zoom), 2 * zoom)
                  for y in range(0, m["ny"], STRIP)]
    strips = pool.map(_key_strip, tasks, chunksize=1)
    pos = 0
    for k, m in enumerate(meta):
        n = math.ceil(m["ny"] / STRIP)
        np.save(key_dir / f"k{k}.npy", np.concatenate(strips[pos:pos + n]))
        pos += n
    return meta


def _start_zoom(st: dict, target: tuple) -> float:
    """The page opens on the whole set, which on a portrait frame is mostly the black inside of the
    set - a weak first second. Probe a coarse frame at doubling zooms and open on the first one where
    under a quarter of the picture is inside the set."""
    for start in (1, 2, 4, 8, 16, 32, 64):
        v = _view(0, 1, {**st, "base": st["magnify"] * start}, target, 1.0)
        c = complex(*target) + _offsets(54, 96, v)
        if (_iterate(c.ravel(), 0j, v["max_iter"], v["zoom"]) < 0).mean() < 0.25:
            return start
    return 64


def render(out: Path, w: int, h: int, fps: int, seconds: float, style: dict, target: str,
           speed: float = 1.0, quality: str = "fast") -> None:
    """Near-lossless intermediate at w x h (ultrafast: x264 would otherwise eat the cores the frames
    need); the caller upscales and burns captions. style and target come from pick()."""
    # speed scales every motion - zoom, spin, glide, colour cycling, warp; 0 = a still frame.
    st, tgt = {**style, "speed": max(speed, 0.0)}, TARGETS[target]
    st["base"] = st["magnify"] * _start_zoom(st, tgt)  # opening zoom; glide still uses magnify
    zoom_end = min(math.exp(ZOOM_RATE * st["speed"] * seconds), 1e11)  # float64 limit
    frames = max(1, round(seconds * fps))
    key_dir = out.parent / "drift_keys"
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
         "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-pix_fmt", "yuv444p", str(out)],
        stdin=subprocess.PIPE,
    )
    try:
        # spawn, not fork: the bot calls this from a worker thread, and forking a threaded process is unsafe.
        with multiprocessing.get_context("spawn").Pool(os.cpu_count() or 4) as pool:
            if quality == "full":
                jobs = ((w, h, i / fps, seconds, st, tgt, zoom_end) for i in range(frames))
                bufs = pool.imap(_frame, jobs, chunksize=2)
            else:
                key_dir.mkdir(exist_ok=True)
                meta = _keyframes(key_dir, w, h, fps, seconds, st, tgt, zoom_end, frames, pool)
                jobs = ((w, h, i / fps, seconds, st, tgt, zoom_end, key_dir, meta) for i in range(frames))
                bufs = pool.imap(_key_frame, jobs, chunksize=4)
            for buf in bufs:
                ff.stdin.write(buf)
    finally:
        ff.stdin.close()
        shutil.rmtree(key_dir, ignore_errors=True)
        if ff.wait() != 0:
            raise RuntimeError(f"ffmpeg failed encoding {out.name}")
