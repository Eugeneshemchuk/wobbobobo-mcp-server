"""Fractal Drift (eugeneshemchuk.github.io/Portfolio/fractal) as a video background: a Mandelbrot
dive into a boundary "river" with slow spin, smooth colouring and the site's styles. A numpy port
of the page's shader, one process per frame, piped to ffmpeg. float64 is good to ~1e12x zoom.
"""

import math
import os
import shutil
import subprocess
import multiprocessing
from pathlib import Path

import numpy as np

TARGETS = {
    "seahorse": (-0.743643887037151, 0.131825904205330),
    "elephant": (0.2869318688950451, 0.014286693904085),
    "bulb": (-1.25066, 0.02012),
}
OVERVIEW = (-0.6, 0.0)
# Zoom pace at speed 1: e^0.52 = 1.68x per second, ~500x over 12s - the pace of the first drift clips.
ZOOM_RATE = 0.52
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
    zoom = math.exp(math.log(zoom_end) * sec / total) * st["magnify"]
    sec *= st["speed"]  # the rest of the motion runs on scaled time too
    glide = math.exp(-GLIDE * sec)
    f = (math.log(zoom) - math.log(WARP_FADE[0])) / math.log(WARP_FADE[1] / WARP_FADE[0])
    f = min(max(f, 0), 1)
    warp = st["warp"] * (1 - f * f * (3 - 2 * f))
    return dict(
        zoom=zoom,
        sx=(target[0] - OVERVIEW[0]) / 2.6 * st["magnify"] * glide,
        sy=(target[1] - OVERVIEW[1]) / 2.6 * st["magnify"] * glide,
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
KEY_DENSITY = math.sqrt(2)  # frames range from 1.4x downsampled to 1.4x upsampled from their keyframe
STRIP = 64  # keyframe rows per task

_keys: dict = {}  # per worker process: keyframe index -> memory-mapped escape counts


def _key_strip(args) -> np.ndarray:
    target, nx, ny, px, y0, y1, max_iter, zoom = args
    ys, xs = np.mgrid[y0:y1, 0:nx]
    c = complex(*target) + (xs + 0.5 - nx / 2) * px + 1j * (ny / 2 - (ys + 0.5)) * px
    return _iterate(c.ravel(), 0j, max_iter, zoom).reshape(y1 - y0, nx)


def _sample(key: np.ndarray, px: float, off: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear lookup weighted by escape, so the set's edge stays clean instead of smearing."""
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
    return total / np.maximum(weight, 1e-6), weight > 0.5


def _key_frame(args) -> bytes:
    w, h, sec, total, st, target, zoom_end, key_dir, meta = args
    v = _view(sec, total, st, target, zoom_end)
    v["z0"] = 0j
    k = _key_index(v["zoom"], st, meta)
    if k not in _keys:
        _keys[k] = np.load(key_dir / f"k{k}.npy", mmap_mode="r")
    sm, esc = _sample(_keys[k], meta[k]["px"], _offsets(w, h, v).ravel())
    return _colour(sm, esc, w, h, st, sec, v["max_iter"])


def _key_index(zoom: float, st: dict, meta: list) -> int:
    return min(max(int(math.log2(zoom / st["magnify"]) + 1e-9), 0), len(meta) - 1)


def _keyframes(key_dir: Path, w: int, h: int, fps: int, seconds: float, st: dict, target: tuple,
               zoom_end: float, frames: int, pool) -> list[dict]:
    count = max(1, math.ceil(math.log2(zoom_end) - 1e-9))
    meta = [dict(ex=0.0, ey=0.0) for _ in range(count)]
    # Each keyframe covers every frame that samples it - the affine view puts the extremes at the corners.
    xs, ys = np.array([0, w - 1, 0, w - 1]), np.array([0, 0, h - 1, h - 1])
    for i in range(frames):
        v = _view(i / fps, seconds, st, target, zoom_end)
        m = meta[_key_index(v["zoom"], st, meta)]
        corners = _offsets(w, h, v, xs, ys)
        m["ex"] = max(m["ex"], np.abs(corners.real).max())
        m["ey"] = max(m["ey"], np.abs(corners.imag).max())
    tasks = []
    for k, m in enumerate(meta):
        zoom = st["magnify"] * 2 ** k
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


def render(out: Path, w: int, h: int, fps: int, seconds: float, style_name: str, target: str,
           speed: float = 1.0, quality: str = "fast") -> None:
    """Near-lossless intermediate at w x h (ultrafast: x264 would otherwise eat the cores the frames
    need); the caller upscales and burns captions."""
    # speed scales every motion - zoom, spin, glide, colour cycling, warp; 0 = a still frame.
    st, tgt = {**STYLES[style_name], "speed": max(speed, 0.0)}, TARGETS[target]
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
