"""audio=ambient: a meditative bed written as MIDI and rendered by FluidSynth with the General MIDI
soundfont - a held pad drone, slow pad chords, sparse bells, deep reverb - plus a sine sub with a
binaural beat. Everything is drawn from the job id, so every clip gets its own track (TikTok flags
identical audio across uploads). Renders in about a second.
"""

import math
import random
from pathlib import Path

import mido

import media

SOUNDFONT = "/usr/share/sounds/sf2/FluidR3_GM.sf2"
TPB = 480  # ticks per beat; tempo is 60 bpm, so one beat = one second
# GM programs, 0-based.
DRONES = {"warm pad": 89, "bowed pad": 92, "halo pad": 94}
PADS = {"new age pad": 88, "choir pad": 91, "halo pad": 94, "sweep pad": 95}
BELLS = {"vibraphone": 11, "tubular bells": 14, "celesta": 8}
# Chords as semitones over the root: open fifths, sus2/sus4 and minor 7/9 voicings - no thirds that
# pull anywhere, so it floats.
CHORDS = [[0, 7, 14], [0, 7, 10, 14], [-4, 3, 7, 10], [5, 12, 14, 19], [-2, 5, 9, 14], [0, 5, 7, 12]]
PENTA = [0, 2, 5, 7, 9]  # major pentatonic over the root: bells never clash


def _events(seconds: float, rng: random.Random) -> tuple[list, dict]:
    root = rng.randrange(36, 44)  # C2 - G2
    drone, pad, bell = rng.choice(list(DRONES)), rng.choice(list(PADS)), rng.choice(list(BELLS))
    ev: list[tuple[float, int, mido.Message]] = []

    def at(t: float, msg: mido.Message, prio: int = 1) -> None:
        ev.append((t, prio, msg))

    for ch, program in ((0, DRONES[drone]), (1, PADS[pad]), (2, BELLS[bell])):
        at(0, mido.Message("program_change", channel=ch, program=program), 0)
        at(0, mido.Message("control_change", channel=ch, control=91, value=127), 0)  # reverb send
        at(0, mido.Message("control_change", channel=ch, control=93, value=70), 0)  # chorus send

    # Drone: root + fifth below the pads for the whole clip, breathing on the expression controller.
    for note in (root, root + 7):
        at(0, mido.Message("note_on", channel=0, note=note, velocity=90))
        at(seconds, mido.Message("note_off", channel=0, note=note))
    breath = rng.uniform(6, 10)  # seconds per swell
    t = 0.0
    while t < seconds:
        swell = 0.5 - 0.5 * math.cos(2 * math.pi * t / breath)
        at(t, mido.Message("control_change", channel=0, control=11, value=int(80 + 40 * swell)))
        t += 0.25

    # Pads: slow chord changes an octave up, overlapping so there is never a gap.
    chord_s = rng.choice([4.0, 5.0, 6.0])
    progression = rng.sample(CHORDS, 4)
    t, i = 0.0, 0
    while t < seconds:
        for semis in progression[i % 4]:
            note = root + 12 + semis
            at(t, mido.Message("note_on", channel=1, note=note, velocity=rng.randrange(45, 65)))
            at(min(t + chord_s + 0.8, seconds), mido.Message("note_off", channel=1, note=note))
        t, i = t + chord_s, i + 1

    # Bells: sparse, soft, high, panned around - like singing bowls struck now and then.
    t = rng.uniform(0.5, 1.5)
    while t < seconds - 1:
        note = root + 36 + rng.choice(PENTA) + rng.choice([0, 12])
        at(t, mido.Message("control_change", channel=2, control=10, value=rng.randrange(30, 98)))
        at(t, mido.Message("note_on", channel=2, note=note, velocity=rng.randrange(30, 60)))
        at(min(t + 3, seconds), mido.Message("note_off", channel=2, note=note))
        t += rng.uniform(1.5, 4.0)
    return ev, dict(root=root, drone=drone, pad=pad, bell=bell)


def render(work: Path, seconds: float, seed: str, sub_level: float = 0.15) -> Path:
    """work/music.wav (48kHz stereo, seconds long). Returns its path."""
    out = work / "music.wav"
    if out.exists():
        return out
    rng = random.Random(seed)
    events, info = _events(seconds, rng)
    mid = mido.MidiFile(ticks_per_beat=TPB)
    track = mido.MidiTrack()
    mid.tracks.append(track)
    track.append(mido.MetaMessage("set_tempo", tempo=1_000_000))  # 60 bpm
    last = 0
    for t, _prio, msg in sorted(events, key=lambda e: (e[0], e[1])):
        tick = round(t * TPB)
        track.append(msg.copy(time=tick - last))
        last = tick
    mid.save(work / "music.mid")

    synth = work / "music.synth.wav"
    media.run([
        "fluidsynth", "-ni", "-q", "-g", "0.3", "-r", "48000", "-F", str(synth),
        "-o", "synth.reverb.room-size=0.95", "-o", "synth.reverb.damp=0.3",
        "-o", "synth.reverb.width=100", "-o", "synth.reverb.level=0.9",
        SOUNDFONT, str(work / "music.mid"),
    ])
    # Deep sine sub on the root, left and right 4 Hz apart (a theta-range binaural beat).
    f = round(440 * 2 ** ((info["root"] - 12 - 69) / 12), 3)
    media.run([
        "ffmpeg", "-y", "-v", "error", "-i", str(synth),
        "-filter_complex",
        f"[0:a]apad,atrim=duration={seconds}[s];"
        f"aevalsrc='{sub_level}*sin(2*PI*{f}*t)|{sub_level}*sin(2*PI*{f + 4}*t)':s=48000:d={seconds}[sub];"
        # Levelled to the voiceover's loudness, so music_level is the real balance under the voice.
        "[s][sub]amix=inputs=2:normalize=0,loudnorm=I=-16:TP=-2:LRA=11[a]",
        "-map", "[a]", "-ar", "48000", "-ac", "2", str(out),
    ])
    synth.unlink()
    return out
