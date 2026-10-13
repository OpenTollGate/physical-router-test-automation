#!/usr/bin/env python3
"""kokoro-tts — synthesize one narration line with Kokoro-82M (kokoro-onnx).

Thin CLI around the offline, CPU-capable Kokoro-82M model so the film
post pass (tools/narrate-film.sh) has a portable voice on Linux, where
the macOS `say` facility does not exist. Reads the line from --text or
stdin, writes a mono WAV at --out.

The model file and the voice pack are NOT shipped here; fetch them once
and pin their sha256 (the provisioning role in ansible/ does both), then
point this script at them with --model/--voices or the KOKORO_MODEL /
KOKORO_VOICES environment variables.

Usage:
  kokoro-tts.py --out line.wav --voice af_heart --text "Staging packages."
  echo "Rebooting the router." | kokoro-tts.py --out line.wav

Environment:
  KOKORO_MODEL   model path (default: kokoro-v1.0.onnx)
  KOKORO_VOICES  voice pack path (default: voices-v1.0.bin)
  KOKORO_VOICE   voice id (default: af_heart)
  KOKORO_LANG    language id (default: en-us)
  KOKORO_SPEED   speaking rate (default: 1.0)
"""

from __future__ import annotations

import argparse
import os
import sys


def main() -> int:
    p = argparse.ArgumentParser(description="Synthesize a line with Kokoro-82M.")
    p.add_argument("--out", required=True, help="WAV file to write")
    p.add_argument("--text", default=None, help="text to speak; defaults to stdin")
    p.add_argument("--model", default=os.environ.get("KOKORO_MODEL", "kokoro-v1.0.onnx"))
    p.add_argument("--voices", default=os.environ.get("KOKORO_VOICES", "voices-v1.0.bin"))
    p.add_argument("--voice", default=os.environ.get("KOKORO_VOICE", "af_heart"))
    p.add_argument("--lang", default=os.environ.get("KOKORO_LANG", "en-us"))
    p.add_argument("--speed", type=float, default=float(os.environ.get("KOKORO_SPEED", "1.0")))
    args = p.parse_args()

    text = args.text if args.text is not None else sys.stdin.read()
    if not text.strip():
        print("kokoro-tts: empty text", file=sys.stderr)
        return 2

    for path in (args.model, args.voices):
        if not os.path.exists(path):
            print(
                f"kokoro-tts: missing {path} — fetch the model and voice pack and "
                "pin their sha256 (see ansible/roles/film_voice_linux)",
                file=sys.stderr,
            )
            return 2

    try:
        from kokoro_onnx import Kokoro
        import soundfile as sf
    except ImportError as exc:
        print(f"kokoro-tts: {exc} — install kokoro-onnx and soundfile", file=sys.stderr)
        return 2

    kokoro = Kokoro(args.model, args.voices)
    samples, sample_rate = kokoro.create(
        text, voice=args.voice, speed=args.speed, lang=args.lang
    )
    sf.write(args.out, samples, sample_rate)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
