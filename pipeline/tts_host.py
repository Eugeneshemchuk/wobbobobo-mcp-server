"""Voiceover service - runs on the macOS host, not in Docker: the worker (a Linux VM) has no `say`.

    python3 tts_host.py          # stdlib only; leave it running next to docker compose

POST /say {"text": ..., "voice": "Zoe (Premium)", "rate": 175} -> 48kHz mono WAV.
GET /voices -> installed voices, one per line. Listens on 127.0.0.1 only.
For realistic voices download a Premium/Enhanced one: System Settings -> Accessibility ->
Spoken Content -> System voice -> Manage Voices (e.g. English: Zoe, Ava, Evan).
"""

import json
import subprocess
import tempfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
MAX_CHARS = 2000


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/voices":
            return self.send_error(404)
        out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        self._reply(200, "text/plain", out.encode())

    def do_POST(self) -> None:
        if self.path != "/say":
            return self.send_error(404)
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            text, voice, rate = str(req["text"])[:MAX_CHARS], str(req["voice"]), int(req.get("rate", 175))
        except (ValueError, KeyError) as e:
            return self._reply(400, "text/plain", f"bad request: {e}".encode())
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "out.wav"
            # Text via stdin, never argv: a leading "-" would be read as an option.
            proc = subprocess.run(
                ["say", "-v", voice, "-r", str(rate), "--file-format=WAVE", "--data-format=LEI16@48000",
                 "-o", str(wav), "-f", "-"],
                input=text, capture_output=True, text=True,
            )
            if proc.returncode != 0 or not wav.exists():
                return self._reply(500, "text/plain", f"say failed: {proc.stderr.strip()}".encode())
            self._reply(200, "audio/wav", wav.read_bytes())

    def _reply(self, code: int, ctype: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}")


if __name__ == "__main__":
    print(f"voiceover service on 127.0.0.1:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
