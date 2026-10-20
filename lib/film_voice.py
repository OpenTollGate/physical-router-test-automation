"""film_voice — narrate a recorded film with a local voice backend.

Any test lane that produced a silent video plus a narration timeline calls
``narrate()`` to get a narrated MP4.
The voice backend is portable: the macOS ``say`` facility or the offline
Kokoro-82M model (the default), selected by ``FILM_TTS_ENGINE``.

The narration timeline is a JSON object ``{"entries": [{"start": <ms>,
"end": <ms>, "say": "<line>"}]}`` — the same shape the film chrome emits in
``Amperstrand/test-films`` (``templates/film-chrome.ts``).

Python::

    from pathlib import Path
    from lib.film_voice import narrate
    narrate(Path("film.mp4"), Path("narration.json"), Path("film-narrated.mp4"))

CLI::

    python3 scripts/film-narrate.py --video film.mp4 \
        --timeline narration.json --out film-narrated.mp4
"""

from __future__ import annotations

import os
import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
NARRATOR = REPO_ROOT / "tools" / "film" / "narrate-film.sh"


class FilmVoiceError(RuntimeError):
    """The narration lane could not produce a narrated film."""


def narrate(
    video: pathlib.Path,
    timeline: pathlib.Path,
    out: pathlib.Path,
    engine: str | None = None,
    voice: str | None = None,
) -> pathlib.Path:
    """Mix synthesized narration onto ``video`` and write ``out``.

    ``engine`` defaults to ``$FILM_TTS_ENGINE`` or ``kokoro`` (offline).
    ``voice`` is a backend-specific voice id (Kokoro: ``af_heart``).
    Raises ``FilmVoiceError`` when the tools or the backend are missing.
    """
    video, timeline, out = pathlib.Path(video), pathlib.Path(timeline), pathlib.Path(out)
    for label, path in (("video", video), ("timeline", timeline)):
        if not path.exists():
            raise FilmVoiceError(f"no {label} at {path}")
    if not NARRATOR.exists():
        raise FilmVoiceError(f"narrator missing at {NARRATOR}")

    env = dict(os.environ)
    env.setdefault("TTS_ENGINE", engine or os.environ.get("FILM_TTS_ENGINE") or "kokoro")
    # The Kokoro backend is a provisioned command (see ansible/film-voice.yml);
    # a checkout can override KOKORO_BIN to run tools/film/kokoro-tts.py directly.
    env.setdefault("KOKORO_BIN", "kokoro-tts")

    cmd = ["bash", str(NARRATOR), str(video), str(timeline), str(out)]
    if voice:
        cmd.append(voice)
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1800)
    if proc.returncode != 0 or not out.exists():
        raise FilmVoiceError(
            f"narrate-film failed (rc={proc.returncode}): {proc.stderr.strip()[-400:]}"
        )
    return out
