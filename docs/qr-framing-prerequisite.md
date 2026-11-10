# Offline QR framing prerequisite — 2026-11-10

**Measured fixture only; native recognition remains unverified.** No launch mode
was added. The recognition-only adapter still refuses all launches. A future
opt-in mode needs separate explicit disposable-AVD/synthetic-payload authorization,
source/artifact linkage and exact pixel-payload validation before launch, with
negative tests for absent authorization and invalid/mismatched provenance.

## Evidence and geometry

Two separately authorized native runs launched the scanner but did not recognize
the synthetic QR. This follow-up used only saved images/inputs; no emulator boot,
device command, lab operation, networking changes or funds operation.

All bounds are half-open screenshot pixels. Original image: 320×320 RGB, dark
module bounds (31,31)-(289,289). Screen: 540×1200. Preview XML bounds:
(36,460)-(504,928), 468 square. Visible frame straight-edge interior:
(78,502)-(462,886), 384 square. **Visible decoration is not proven decoder ROI.**

Empirical uniform-scale image registration against the actual screenshots:

- Original first input: screen ≈ (35,357) + 2.590 × original coordinates;
  sampled mismatch 0.184%. Predicted module bounds extend outside the preview.
- Second input: 1280-square white canvas, original at (480,480). Screen ≈
  (347.5,667.5) + 0.649 × embedded-original coordinates; mismatch 1.843%.
  Predicted module right edge x535 exceeds preview x504 by about 31 px.
  Quarter-turn alternatives mismatch 32.8–38.8%, supporting upright orientation.

The new local fixture keeps the original 320×320 RGB pixels **unchanged**, upright,
on the same 1280-square canvas at **(201,361)**. This offset solves for the measured
frame centre (270,694), not a guessed padding increment. Conditional on the same
mapping, full-image bounds are (166.429,590.269)-(374.109,797.949), with predicted
left/top/right/bottom clearances 88.429/88.269/87.891/88.051 px. These are predictions,
not observations of a new runtime screenshot.

Retained Android14 scanner source returns TextureView size, constructs
`Rect(0,0,previewSize.getHeight(),previewSize.getHeight())` in `getFramePosition`,
and applies the supplied matrix with `setTransform`. Recorded rear camera is
`imagefile`, front is `none`, screen rotation is 0. Actual camera buffer size,
texture matrix and decoder-crop mapping are not retained. The empirical fit does
not establish those undocumented runtime values or source/installed-APK identity.

## Artifact identity and offline verification

Local evidence directory:
`~/reports/qr-native-20261110/measured-framing-20261110/`
contains `measure.py`, `REPORT.md`, `measurements.json`, `sha256.json`, and
`measured-camera.png`. Raw screenshots remain local, not committed.

- Original PNG SHA256:
  `1cca6e4031a8c2f6641e4ffb7c3f076ec97b8e239e61eb26d33fd08ca1161648`.
- Measured fixture SHA256:
  `73c92b9eb8bf26ea6bec3c5d3eddd39db2b3e4c04e634059cf5c5db1a2fc4ab4`.
- Retained ZXing-cpp 3.1.1 independently decoded the original before construction
  and saved fixture afterward to exactly
  `WIFI:T:WPA;S:PRTA-QR-SYNTHETIC;P:synthetic-only-123;H:false;;`.
- Parent reran the deterministic measurement/decode assertions: exit 0.
- Host scoped adapter/ADB tests: 58 passed; coverage run/report exit 0,
  65 statements / 1 missed, 20 branches / 1 partial, 98% combined coverage.
  This is not full-repository, native-runtime or branch-only coverage.

## Evidence required before another materially different attempt

A separately authorized runtime test must bind the fixture hash and camera
configuration, establish camera-buffer/orientation/texture/decoder-crop mapping,
and capture an unaltered screenshot proving full QR plus quiet zone containment
in that mapped crop. Compare actual bounds to the prediction; do not keep tuning
margins blindly. Fresh headroom/ownership and explicit synthetic save/enable/join
side-effect authorization are prerequisites, not implied by host decode.

Only subsequent native recognition and independent saved/enabled/association
observations can establish those stages. DHCP/captive evidence is required only
if reached; factory AndroidWifi/NAT is unrelated. No physical RF acceptance,
merge readiness or runtime success follows from this fixture.
