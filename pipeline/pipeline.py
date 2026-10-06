"""Stateless job runner. A job is a folder; each step skips if its output already exists,
so re-running a failed job resumes where it stopped.

jobs/<id>/
  input_00.mp4 ...      raw clips in upload order
  text.txt              text-only job instead of clips: hook card + fractal zoom, no footage
  voice.audio           voice message; transcribed into text.txt
  work/voiceover.wav    text read by a macOS voice (tts_host.py); captions follow its timing
  quote.txt             optional user-supplied quote
  flags.json            optional per-job overrides of content/style.toml (key=value flags)
  transcript.json       per-clip word timings
  plan.json             segments + quote + caption
  work/                 intermediates
  final.mp4
  publish.json          TikTok publish_id + status
  status                last completed step, or "failed: ..."

Run one job by hand: python pipeline.py data/jobs/<id>
"""

import json
import sys
from pathlib import Path
from typing import Callable

import media
import render
import style
import tiktok
import tts
from plan import Plan, make_plan
from transcribe import transcribe

Notify = Callable[[str], None]


def _set_status(job: Path, status: str) -> None:
    (job / "status").write_text(status)


def _user_quote(job: Path) -> str | None:
    qf = job / "quote.txt"
    return (qf.read_text().strip() if qf.exists() else None) or None


def process(job: Path, notify: Notify = print) -> Path:
    """Everything up to final.mp4."""
    work = job / "work"
    work.mkdir(exist_ok=True)
    voice = job / "voice.audio"
    if voice.exists() and not (job / "text.txt").exists():
        notify("Transcribing voice...")
        text = " ".join(w["w"] for w in transcribe(voice, work)).strip()
        if not text:
            raise RuntimeError("no speech found in the voice message")
        (job / "text.txt").write_text(text)
        _set_status(job, "transcribed")
    if (job / "text.txt").exists():
        return _process_text(job, work, notify)
    clips = sorted(job.glob("input_*.mp4"))
    if not clips:
        raise RuntimeError("no input clips in job folder")

    tf = job / "transcript.json"
    if not tf.exists():
        notify(f"Transcribing {len(clips)} clip(s)...")
        tf.write_text(json.dumps([
            {"duration": media.probe(c)["duration"], "words": transcribe(c, work)} for c in clips
        ]))
        _set_status(job, "transcribed")
    clip_data = json.loads(tf.read_text())

    pf = job / "plan.json"
    if not pf.exists():
        notify("Picking segments and quote...")
        pf.write_text(make_plan(clip_data, _user_quote(job), seed=job.name).model_dump_json(indent=2))
        _set_status(job, "planned")
    plan = Plan.model_validate_json(pf.read_text())

    final = job / "final.mp4"
    if not final.exists():
        notify("Rendering...")
        joined = render.cut_segments(clips, plan.segments, work)
        duration = sum(s.end - s.start for s in plan.segments)
        words = render.remap_words([c["words"] for c in clip_data], plan.segments)
        st = style.load(job)
        ass = render.build_ass(words, plan.quote, plan.quote_author, duration, st, quote_fit=plan.quote_fit)
        tmp = work / "final.tmp.mp4"
        render.finalize(joined, ass, tmp, st, duration=duration)
        tmp.rename(final)  # atomic: final.mp4 only exists when complete
        _set_status(job, "rendered")
    return final


def _process_text(job: Path, work: Path, notify: Notify) -> Path:
    pf = job / "plan.json"
    if not pf.exists():
        notify("Planning...")
        pf.write_text(make_plan([], _user_quote(job), seed=job.name).model_dump_json(indent=2))
        _set_status(job, "planned")
    plan = Plan.model_validate_json(pf.read_text())

    final = job / "final.mp4"
    if not final.exists():
        st = style.load(job)
        text = (job / "text.txt").read_text()
        voice = None
        if st["voiceover"]:
            voice = work / "voiceover.wav"
            if not voice.exists():
                notify("Recording voiceover...")
                tts.speak(text, voice, st["voice"], st["voice_rate"])
            words = _spoken_words(text, voice, work)
        else:
            words = render.timed_words(text, st["pace"])
        notify("Rendering fractal...")
        card_s = render.card_duration(words)
        duration = render.text_duration(card_s, st, job.name)
        # Text clips show only a quote the user asked for ("quote: ..."), not one from quotes.txt.
        quote = plan.quote if _user_quote(job) else ""
        ass = render.build_ass(words, quote, plan.quote_author, duration, st, look=st["look"])
        tmp = work / "final.tmp.mp4"
        render.render_text(work, ass, tmp, card_s, duration, st, voice)
        tmp.rename(final)
        _set_status(job, "rendered")
    return final


def _spoken_words(text: str, voice: Path, work: Path) -> list[dict]:
    """Caption timings from the voiceover itself, shifted by the lead-in it is mixed at. Whisper's
    timings with the typed words when the counts line up, so captions keep the user's spelling."""
    heard = transcribe(voice, work)
    typed = text.split()
    if len(heard) == len(typed):
        heard = [{**h, "w": t} for h, t in zip(heard, typed)]
    if not heard:
        raise RuntimeError("voiceover came back silent - check the voice name (voice=...)")
    return [{**h, "start": h["start"] + render.LEAD_S, "end": h["end"] + render.LEAD_S} for h in heard]


def publish(job: Path, notify: Notify = print) -> dict:
    pub = job / "publish.json"
    if pub.exists():
        return json.loads(pub.read_text())
    notify("Uploading to TikTok inbox...")
    publish_id = tiktok.upload_to_inbox(job / "final.mp4")
    # Persist before polling so a crash can't cause a duplicate upload.
    pub.write_text(json.dumps({"publish_id": publish_id, "status": "UPLOADED"}))
    status = tiktok.wait_for_inbox(publish_id)
    result = {"publish_id": publish_id, "status": status}
    pub.write_text(json.dumps(result))
    _set_status(job, "published")
    return result


if __name__ == "__main__":
    job_dir = Path(sys.argv[1])
    print(process(job_dir))
    if "--publish" in sys.argv:
        print(publish(job_dir))
