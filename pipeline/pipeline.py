"""Stateless job runner. A job is a folder; each step skips if its output already exists,
so re-running a failed job resumes where it stopped.

jobs/<id>/
  input_00.mp4 ...      raw clips in upload order
  text.txt              text-only job instead of clips: hook card + fractal zoom, no footage
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
        notify("Picking quote...")
        pf.write_text(make_plan([], _user_quote(job), seed=job.name).model_dump_json(indent=2))
        _set_status(job, "planned")
    plan = Plan.model_validate_json(pf.read_text())

    final = job / "final.mp4"
    if not final.exists():
        notify("Rendering fractal...")
        st = style.load(job)
        words = render.timed_words((job / "text.txt").read_text(), st["pace"])
        card_s = render.card_duration(words)
        duration = render.text_duration(card_s, st)
        ass = render.build_ass(words, plan.quote, plan.quote_author, duration, st, look=st["look"])
        tmp = work / "final.tmp.mp4"
        render.render_text(work, ass, tmp, card_s, duration, st)
        tmp.rename(final)
        _set_status(job, "rendered")
    return final


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
