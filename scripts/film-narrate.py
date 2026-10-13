#!/usr/bin/env python3
"""Narrate any recorded film run with a local voice backend.

Adds a synthesized voice-over to a silent recording from a narration
timeline, so any test that produced footage plus events can also produce a
narrated film. See ``lib/film_voice.py`` for the timeline shape and the
backend selection (``FILM_TTS_ENGINE``: ``kokoro`` default, ``say``,
``espeak``).

Usage:
  python3 scripts/film-narrate.py --video clip.mp4 --timeline narration.json \
      --out clip-narrated.mp4 [--engine kokoro] [--voice af_heart]
"""

from __future__ import annotations

import argparse
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from lib.film_voice import FilmVoiceError, narrate  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Narrate a recorded film.")
    ap.add_argument("--video", required=True, help="silent recording (mp4/webm)")
    ap.add_argument("--timeline", required=True, help="narration timeline JSON")
    ap.add_argument("--out", required=True, help="narrated mp4 to write")
    ap.add_argument("--engine", default=None, help="say | kokoro | espeak")
    ap.add_argument("--voice", default=None, help="backend voice id")
    args = ap.parse_args()

    try:
        out = narrate(
            pathlib.Path(args.video),
            pathlib.Path(args.timeline),
            pathlib.Path(args.out),
            engine=args.engine,
            voice=args.voice,
        )
    except FilmVoiceError as exc:
        print(f"film-narrate: {exc}", file=sys.stderr)
        return 1
    print(f"narrated film: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
