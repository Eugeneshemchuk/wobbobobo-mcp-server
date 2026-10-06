"""Style and effect settings: content/style.toml, overridden per job by key=value flags
in the Telegram text or caption (saved as jobs/<id>/flags.json)."""

import json
import re
import tomllib
from pathlib import Path

import config

FLAG = re.compile(r'(?<!\S)([a-z_]+)=(?:"([^"]*)"|(\S+))(?!\S)', re.IGNORECASE)
HEX = re.compile(r"#[0-9a-fA-F]{6}")
TRUE, FALSE = {"1", "true", "on", "yes"}, {"0", "false", "off", "no"}
CHOICES = {
    "audio": {"ambient", "gen", "bass", "drone", "off"}, "look": {"neon", "pixel", "drift"}, "hook": {"glitch", "shake", "both", "off"},
    "drift_style": {"classic", "midnight", "ocean", "ember", "neon", "topo"},
    "drift_target": {"seahorse", "elephant", "bulb"},
    "drift_quality": {"fast", "full"},
    "tts": {"kokoro", "say"},
}
NOTE = re.compile(r"([A-G])([#b]?)(-?\d)")
SEMITONE = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}


def defaults() -> dict:
    data = tomllib.loads((config.CONTENT_DIR / "style.toml").read_text(encoding="utf-8"))
    flat: dict = {}
    for section in data.values():  # sections are only for reading; keys are unique
        flat.update(section)
    return flat


def note_hz(name: str) -> float:
    """Scientific pitch ("A1", "C#2", "Bb0") -> Hz, A4 = 440."""
    m = NOTE.fullmatch(name)
    if not m:
        raise ValueError(f"not a note: {name}")
    letter, acc, octave = m.groups()
    semis = SEMITONE[letter] + {"#": 1, "b": -1, "": 0}[acc] + (int(octave) - 4) * 12
    return round(440 * 2 ** (semis / 12), 3)


def _coerce(raw: str, default, key: str = ""):
    if isinstance(default, bool):
        if raw.lower() not in TRUE | FALSE:
            raise ValueError("expected on/off")
        return raw.lower() in TRUE
    try:
        if isinstance(default, int):
            return int(raw)
        if isinstance(default, float):
            return float(raw)
    except ValueError:
        raise ValueError("expected a whole number" if isinstance(default, int) else "expected a number") from None
    if key in CHOICES and raw.lower() not in CHOICES[key]:
        raise ValueError(f"expected one of {', '.join(sorted(CHOICES[key]))}")
    if key == "bass_notes":
        for n in raw.split():
            note_hz(n)
        if not raw.split():
            raise ValueError("expected notes like A1 C2")
    if key.endswith("_palette"):
        if len(raw.split()) < 2 or not all(HEX.fullmatch(c) for c in raw.split()):
            raise ValueError('expected 2+ colours like "#000000 #8C3CFF #BEFF28"')
        return raw
    if isinstance(default, str) and default.startswith("#") and not HEX.fullmatch(raw):
        raise ValueError("expected a colour like #FFE500")
    return raw.replace(",", " ")  # commas would break the ASS style line


def extract_flags(text: str) -> tuple[str, dict]:
    """Split key=value flags out of text. Raises ValueError on unknown keys or bad values."""
    base = defaults()
    flags = {}
    for m in FLAG.finditer(text):
        key, raw = m.group(1).lower(), m.group(2) if m.group(2) is not None else m.group(3)
        if key not in base:
            raise ValueError(f"Unknown flag {key}=. Known: {', '.join(sorted(base))}")
        try:
            flags[key] = _coerce(raw, base[key], key)
        except ValueError as e:
            raise ValueError(f"Bad value for {key}: {raw} ({e})") from None
    return FLAG.sub("", text), flags


def save_flags(job: Path, flags: dict) -> None:
    if flags:
        (job / "flags.json").write_text(json.dumps(flags))


def load(job: Path) -> dict:
    st = defaults()
    ff = job / "flags.json"
    if ff.exists():
        st.update(json.loads(ff.read_text()))
    return st


def rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def ass_color(color: str) -> str:
    r, g, b = rgb(color)
    return f"&H00{b:02X}{g:02X}{r:02X}&"  # ASS is &HAABBGGRR
