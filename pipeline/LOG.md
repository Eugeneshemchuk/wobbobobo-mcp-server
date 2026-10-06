# Pipeline log

## 2026-10-07 - state after the voice-prompt / drift work

**Repo**
- Branch `dev`, created from main at 4c6f99d (merge of PR #6 `voice-prompt`). It is clean and local only, not pushed yet.
- The `voice-prompt` branch has been deleted locally and on GitHub.
- From now on, all work happens on `dev`.

**What the pipeline does now**
- Telegram input:
  - video -> clip (unchanged)
  - text or voice message -> 9:16 fractal "drift" clip with captions, voice and music
- Voice messages are transcribed with faster-whisper (small). The sender's own voice is cleaned (highpass, silence trim, -16 LUFS) and used as the voiceover.
- Typed text gets a Kokoro-82M voiceover (ONNX, CPU, inside the worker):
  - voice `am_onyx`, speed 0.8
  - `tts=say`, which uses macOS `say` through `tts_host.py` on the host, is still available as an option
- No motivational quote on text or voice clips unless the user supplies one.
- Captions use word timings from Whisper run on the voice track.

**Look (`drift.py`)**
- A numpy port of the site's Fractal Drift shader. Keyframe fast mode renders one keyframe per 2x zoom and interpolates between them, about 30x faster than full mode.
- Each job picks its own style and target, seeded by the job id:
  - 6 site styles or a generated gradient
  - 7 curated targets
  - speed 0.8
- A start-zoom probe and a glide clamp prevent black openings.

**Audio (`ambient.py`)**
- A MIDI-generated meditative bed rendered with FluidSynth and the GM soundfont: pad drone, slow chords, sparse bells, and a 4 Hz binaural sine sub.
- It is unique per job, which addresses TikTok's duplicate-audio flag.
- The music sits at 0.25 under the voice, and the final mix is levelled to -14 LUFS.

**Output**
- H.264 at 30 fps, capped at 10 MB (`max_mb`) by a bitrate cap with up to 2 retries.
- Measured results:

  | Clip length | Render time | Size |
  |---|---|---|
  | 11-16 s | about 38 s | 9.1-9.5 MB |
  | 24.5 s | 108 s | 9.3 MB |

  The 24.5 s result comes from the last live job, 20261006-225225-ffabd0.

**Runtime**
- colima VM: 4 CPU, 6 GB RAM, 40 GB disk.
- Containers `worker` and `telegram-bot-api` are up. The worker uses about 1.35 GB RAM while rendering.
- Image `pipeline-worker` is 1.62 GB. The old wobbo-pipeline image has been removed.
- `tts_host.py` is running on the Mac (nohup, port 8765). It is only needed for `tts=say`.
- TikTok is not connected, so uploads are skipped. Rendered clips stay in `data/jobs/<id>/final.mp4`.

**Data (`data/`, gitignored)**
- `jobs/`: 31 jobs, 1.1 GB.
- `models/`: 815 MB in total.
  - Whisper: about 464 MB
  - Kokoro: 348 MB, downloaded by hand
- Leftover test folders from earlier sessions, about 340 MB:
  - `basstest`, `fixtest`, `fractaltest`, `hooktest`, `neontest`, `styletest`, `texttest`
  - `ref`
  - the debug `*.png` files

**Known gaps / open items**
- Cleanup doesn't prune the bot's `voice/` and `music/` download folders.
- Kokoro's first-use auto-download hasn't been tested, because the model was downloaded by hand.
- Rendering a longer clip takes much longer than a short one: 24.5 s took 108 s, against about 38 s for 11-16 s.
- `dev` isn't pushed.
- `tts_host.py` can be stopped if `say` is no longer wanted.
- The leftover test folders in `data/` can be deleted.
