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

## Router-driven films: where to record

Not every film needs the hardware. Classify the recorder **before** wiring
anything up — the answer decides which host runs it:

| Class | How to tell | Needs | Run it on |
|---|---|---|---|
| **Scripted** | renders HTML/text (a fake terminal, a static page); no `page.goto(router)` | nothing | any host with `node`+Playwright+`ffmpeg`+Kokoro |
| **Local-UI** | `page.goto(WIZ_URL)` at a `127.0.0.1` URL | a locally served UI | the authoring host, UI served locally |
| **Router-driven** | `page.goto` / `PORTAL_URL` / `BOARD_HOST` at a real address | the live router | a host **on the router's LAN/management side** |

A quick check: read the recorder. If it never navigates to a router address, it
is scripted and hermetic — no router, no tunnel, no firewall hole.

For **router-driven** films, the browser must run where the target is actually
reachable — for the fleet that is the gateway, `cobrador`, on the box's
**management net** (see
[RUNBOOK-bench-lab.md](RUNBOOK-bench-lab.md#reaching-a-tollgate-installed-box-post-install-address)).
Do **not** SSH-tunnel the router's ports to a WAN-side laptop, and do **not**
add temporary firewall accepts; the working path already exists. The portal and
the board are addressed at the **management-net IP**, never the flashed LAN IP.

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

## Concrete lanes

- **`films/installer/`** — the TollGate **install run book** films: the wizard
  (GUI + headless), the merged manual install, and the post-install board +
  payment. The kit carries a `README.md` with the dependency pins, the
  environment knobs, and the per-film determinism — the scripted films are
  byte-reproducible and offline; the portal/board films need the bench routers.
  This is the reference concrete lane the mechanics above were built for.

## References

- `lib/film_voice.py`, `scripts/film-narrate.py`, `scripts/publish-film.py`
- `tools/film/` — the vendored narrator and Kokoro CLI (`PROVENANCE.md`)
- `ansible/film-voice.yml`, `ansible/roles/film_voice/`
- `Amperstrand/test-films` — the methodology this lane follows.

## Reproducible recording environment (IaC)

A router-driven take needs a real guest session and a real token. Do not assemble
that by hand — use the scripts:

- `scripts/film-record-env.sh up --host <recorder> --ssid <guest-ssid> --router-ip <mgmt-ip>`
  joins the recorder host's WiFi to the router's **guest** SSID (never-default, so
  the management path is untouched), disables WiFi power-save, and **primes
  NoDogSplash** by loading the gateway port (without it the purchase is refused
  with `#CU107`). `… down --host <recorder>` restores the WiFi.
- `scripts/mint-testnet-token.sh <out-file> [amount] [mint-url]` (with `CASHU_TS`)
  mints a fresh **testnet** e-cash token for the portal film.

Then record the kit's films (`installer/docs/runbook/film/`) and gate them with
`film-verify.py` before publishing.
