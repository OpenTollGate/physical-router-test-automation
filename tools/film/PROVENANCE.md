# Vendored film tools — provenance

`tools/film/narrate-film.sh` and `tools/film/kokoro-tts.py` are lifted from
[Amperstrand/test-films](https://github.com/Amperstrand/test-films), the
project-agnostic methodology repository for the film practice.

| File | Origin | Pin |
|---|---|---|
| `narrate-film.sh` | `tools/narrate-film.sh` | `23c0660` |
| `kokoro-tts.py` | `tools/kokoro-tts.py` | `23c0660` |

Both files are generic; this repository holds a copy so the narration lane
works without a second checkout.
Change them upstream first, then copy the merged file here and bump the pin in
the same commit, so the copy never drifts silently.

The Kokoro-82M model and voice pack are not vendored.
`ansible/film-voice.yml` fetches them and verifies their sha256.
