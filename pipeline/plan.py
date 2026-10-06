"""Deterministic planning - no LLM calls at runtime.

- Segments: keep speech runs, drop leading filler and dead air, fill clips in order up to TARGET_MAX_S.
- Quote: the user's caption, else a one-line summary - the punchiest phrase said in the kept footage -
  else (no speech) the least-recently-used line from content/quotes.txt.
- TikTok caption + hashtags: sampled from content/captions.txt and content/hashtags.txt.
"""

import random
import time
from pathlib import Path

from pydantic import BaseModel

import config


class Segment(BaseModel):
    clip: int
    start: float
    end: float


class Plan(BaseModel):
    segments: list[Segment]
    quote: str
    quote_author: str | None
    quote_fit: bool = False  # render on one line, shrinking the font to fit (auto summaries)
    tiktok_caption: str
    hashtags: list[str]


FILLERS = {"so", "um", "uh", "uhm", "erm", "er", "ah", "like", "okay", "ok", "well", "yeah", "alright", "right", "and"}
GAP_S = 0.8        # silence longer than this is cut out
PAD_IN, PAD_OUT = 0.1, 0.25
BROLL_MAX_S = 8.0  # cap per clip without speech when mixed with talking clips


# --- segments ---------------------------------------------------------------

def _norm(word: str) -> str:
    return word.strip(".,!?;:\"'").lower()


def _runs(words: list[dict]) -> list[list[dict]]:
    """Split words into sentences: break on end punctuation or a pause longer than GAP_S."""
    runs, cur = [], []
    for w in words:
        if cur and w["start"] - cur[-1]["end"] > GAP_S:
            runs.append(cur)
            cur = []
        cur.append(w)
        if w["w"].endswith((".", "?", "!")):
            runs.append(cur)
            cur = []
    if cur:
        runs.append(cur)
    # Strip leading fillers ("So, um, ...") from each run.
    out = []
    for r in runs:
        while r and _norm(r[0]["w"]) in FILLERS:
            r = r[1:]
        if r:
            out.append(r)
    return out


def pick_segments(clips: list[dict]) -> list[Segment]:
    budget = config.TARGET_MAX_S
    segments: list[Segment] = []
    has_speech = any(c["words"] for c in clips)

    for i, clip in enumerate(clips):
        if budget < 1.0:
            break
        dur = clip["duration"]
        if not clip["words"]:
            # B-roll: share the remaining budget, skip the shaky first half-second.
            remaining_clips = len(clips) - i
            share = budget / remaining_clips if not has_speech else min(BROLL_MAX_S, budget)
            start = min(0.5, dur / 4)
            end = min(dur, start + share)
            segments.append(Segment(clip=i, start=start, end=end))
            budget -= end - start
            continue
        for run in _runs(clip["words"]):
            start = max(0.0, run[0]["start"] - PAD_IN)
            end = min(dur, run[-1]["end"] + PAD_OUT)
            if end - start > budget:
                # Doesn't fit: take it only if nothing is kept yet, truncated at a word edge.
                if segments:
                    break
                fit = [w for w in run if w["end"] + PAD_OUT - start <= budget]
                if not fit:
                    break
                end = fit[-1]["end"] + PAD_OUT
            # Merge with the previous segment if contiguous in the same clip.
            if segments and segments[-1].clip == i and start - segments[-1].end < 0.05:
                segments[-1].end = end
            else:
                segments.append(Segment(clip=i, start=start, end=end))
            budget -= end - start

    if not segments:
        segments = [Segment(clip=0, start=0.0, end=min(clips[0]["duration"], config.TARGET_MAX_S))]
    for s in segments:
        s.start, s.end = round(s.start, 3), round(s.end, 3)
    return segments


# --- summary ----------------------------------------------------------------

# Words that make a phrase weaker as a headline, on top of FILLERS.
WEAK = FILLERS | {"just", "really", "basically", "literally", "bro", "gonna", "gotta", "kinda", "stuff", "know"}
# A headline shouldn't start or end on these.
EDGE = {"i", "you", "we", "it", "the", "a", "an", "to", "of", "in", "on", "at", "with", "and", "but", "or", "so",
        "my", "your", "it's", "is", "was", "for", "that", "this"}
SUMMARY_WORDS = (3, 9)  # min, max words; 6 scores best
PHRASE_GAP_S = 0.4      # a pause this long also ends a phrase


def _phrases(words: list[dict]) -> list[list[dict]]:
    """Sentences, split further at commas and short pauses; stutters ("just just") collapsed."""
    out = []
    for run in _runs(words):
        cur: list[dict] = []
        for w in run:
            if cur and w["start"] - cur[-1]["end"] > PHRASE_GAP_S:
                out.append(cur)
                cur = []
            if cur and _norm(w["w"]) == _norm(cur[-1]["w"]):
                continue
            cur.append(w)
            if w["w"].endswith((",", ";", ":")):
                out.append(cur)
                cur = []
        if cur:
            out.append(cur)
    return out


def _score(phrase: list[dict]) -> float:
    words = [_norm(w["w"]) for w in phrase]
    return (
        -abs(len(words) - 6)
        - 1.5 * sum(w in WEAK for w in words)
        - 2 * (words[0] in EDGE | WEAK)
        - 2 * (words[-1] in EDGE | WEAK)
    )


def summary_line(clips: list[dict], segments: list[Segment]) -> str | None:
    """No caption sent: the punchiest phrase actually said in the kept footage, as one on-screen line."""
    lo, hi = SUMMARY_WORDS
    best: tuple[float, list[dict]] | None = None
    for s in segments:
        words = [w for w in clips[s.clip]["words"] if w["start"] >= s.start and w["end"] <= s.end]
        for phrase in _phrases(words):
            # Short phrases as they are; long unpunctuated runs: every window that fits.
            if len(phrase) <= hi:
                candidates = [phrase] if len(phrase) >= lo else []
            else:
                candidates = [phrase[i:i + n] for n in range(lo, hi + 1) for i in range(len(phrase) - n + 1)]
            for c in candidates:
                score = _score(c)
                if best is None or score > best[0]:  # ties: the earlier phrase wins
                    best = (score, c)
    if not best:
        return None
    text = " ".join(w["w"] for w in best[1]).rstrip(",;:")
    return text[0].upper() + text[1:]


# --- text content -----------------------------------------------------------

def _lines(name: str) -> list[str]:
    path = config.CONTENT_DIR / name
    return [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]


def _used_quotes() -> dict[str, float]:
    """quote text -> last used unix time, from an append-only log."""
    used: dict[str, float] = {}
    if config.QUOTES_USED_FILE.exists():
        for ln in config.QUOTES_USED_FILE.read_text(encoding="utf-8").splitlines():
            ts, _, text = ln.partition("\t")
            used[text] = float(ts)
    return used


def pick_quote(rng: random.Random) -> tuple[str, str | None]:
    """Random quote not used within the cooldown; if all are, the least recently used."""
    entries = []
    for ln in _lines("quotes.txt"):
        text, _, author = ln.partition("|")
        entries.append((text.strip(), author.strip() or None))
    used = _used_quotes()
    cutoff = time.time() - config.QUOTE_COOLDOWN_DAYS * 86400
    fresh = [e for e in entries if used.get(e[0], 0) < cutoff]
    choice = rng.choice(fresh) if fresh else min(entries, key=lambda e: used.get(e[0], 0))
    config.QUOTES_USED_FILE.parent.mkdir(parents=True, exist_ok=True)
    with config.QUOTES_USED_FILE.open("a", encoding="utf-8") as f:
        f.write(f"{time.time():.3f}\t{choice[0]}\n")
    return choice


def make_plan(clips: list[dict], user_quote: str | None, seed: str = "") -> Plan:
    rng = random.Random(seed or None)
    segments = pick_segments(clips) if clips else []  # no clips = text-only job
    fit = False
    if user_quote:
        text, _, author = user_quote.partition("|")
        quote, quote_author = text.strip(), author.strip() or None
    elif line := summary_line(clips, segments):
        quote, quote_author, fit = line, None, True
    else:
        quote, quote_author = pick_quote(rng)
    tags = _lines("hashtags.txt")
    return Plan(
        segments=segments,
        quote=quote,
        quote_author=quote_author,
        quote_fit=fit,
        tiktok_caption=rng.choice(_lines("captions.txt")),
        hashtags=tags[:2] + rng.sample(tags[2:], k=min(3, len(tags) - 2)),
    )
