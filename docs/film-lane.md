# Film lane — a narrated video from any test

Any test lane in this repository can attach a narrated video.
The lane keeps the recording generic (a silent video plus a narration
timeline) and makes the voice a portable, offline backend, so a phone run, a
browser run, or a router-script run produces a watchable film by the same
mechanics.

## What you get

```text
results/films/<id>/
  film.mp4            # the silent recording (any lane)
  narration.json      # the timeline the narrator consumes
  film-narrated.mp4   # the narrated film (this lane)
```

## Why

A recording that only the person who ran the test can read is half a result.
Narration turns a screen capture into a film a reviewer, a newcomer, or an
agent can watch without the rig.
The voice is offline (Kokoro-82M) so it reproduces on any Linux host and costs
nothing per line.

## Provision the backend (once per host)

```sh
ansible-playbook -i ansible/inventory.ini ansible/film-voice.yml
```

This installs `ffmpeg`, `espeak-ng`, a Python venv with `kokoro-onnx`, the
pinned model and voice pack, and a `kokoro-tts` command on `PATH`.
See `ansible/roles/film_voice/defaults/main.yml` for the pins.

## Narrate a recording

The narration timeline is a JSON object, one entry per spoken line, times in
milliseconds:

```json
{"entries": [
  {"start": 1000, "end": 3600, "say": "Staging the package on the router."},
  {"start": 4200, "end": 7200, "say": "Install complete. Rebooting."}
]}
```

```sh
python3 scripts/film-narrate.py --video clip.mp4 --timeline narration.json \
    --out film-narrated.mp4 --engine kokoro --voice af_heart
# or, for a lane that already imports the repo:
python3 -c 'from lib.film_voice import narrate; narrate("clip.mp4","narration.json","out.mp4")'
```

The pytest phone lane opts in with `--film --film-narrate`.
The `--film-narrate` flag records the clip and calls this lane at session end.

## Backends

| `FILM_TTS_ENGINE` | Tool | Notes |
|---|---|---|
| `kokoro` (default) | `tools/film/kokoro-tts.py` | offline, CPU, Apache-2.0, portable |
| `say` | macOS `say` | the historical default; macOS only |
| `espeak` | `espeak-ng` | fast fallback; robotic |

Pin the voice and the model revision in the run manifest, and keep one voice
per audience level across a series.

## Publish

Upload the finished artifacts to Blossom and share the content-addressed URLs:

```sh
python3 scripts/publish-film.py --run results/films/<id> \
    --server https://blossom1.orangesync.tech
```

The installer lane's `installer/publish_film.py` additionally emits the Nostr
index events the test dashboard reads.

## Pitfalls

- Footage from a local or physical lane can carry real SSIDs, MACs, IPs, and
  keys.
  Keep it in the gitignored `results/` tree and gate publishing on the
  `can_publish` flag; never film credentials.
- Do not commit the model or voice pack.
  The playbook fetches and verifies them by sha256.
- A model or voice change re-voices the whole series: bump it deliberately and
  re-render.
- Per-scene clips, never one long take, so a re-cut stays local
  (`tools/film/narrate-film.sh` already synthesizes line by line).

## References

- `lib/film_voice.py`, `scripts/film-narrate.py`, `scripts/publish-film.py`
- `tools/film/` — the vendored narrator and Kokoro CLI (`PROVENANCE.md`)
- `ansible/film-voice.yml`, `ansible/roles/film_voice/`
- `Amperstrand/test-films` — the methodology this lane follows.
