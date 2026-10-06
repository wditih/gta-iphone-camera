# iPhone camera prototype journal

## Scope agreed with the user

- GTA V Legacy, offline Story Mode, scenes prepared in Menyoo.
- iPhone 12 Pro Max, full position and rotation tracking.
- An existing App Store tracker, with a live GTA image on the phone.
- Camera takes must replay and export through Rockstar Editor.
- The user cannot run the game locally; a friend will do in-game acceptance.

## Chosen route

Extend the GPL-3.0 REPlus editor camera hook rather than spawn a camera ped.
The scene is recorded normally. The phone camera is then performed against its
replay; post-filter world poses are stored against the actual replay clock.
Playback and export evaluate that immutable track at clip time, independently
of the export frame rate and the phone connection.

Base: crxhvrd/REPlus at c6eba57 (origin/main when cloned 2026-10-07).
Dedicated branch: codex/iphone-rockstar-camera. Worktree on F.
The Mitsuki website repository is unrelated and is not modified.

## Confirmed from upstream source

- `src/replay/smoothblend.cpp::hkUpdateSmoothing` runs playing and paused, after
  the game's own final camera writer.
- `rdirector` exposes the final right/forward/up basis, position and FOV.
- `game::addr_g_ReplayTimeMs` is the replay clock, not wall time.
- `game::clipIdentities()` provides recording-derived clip identities;
  `game::projectName()` scopes sidecar files to the open project.
- Transport supports play, pause and seeks. No live-game camera natives are
  required for this editor-only prototype.
- BlendMotion 3.3.2 add-on source documents 51/59-byte little-endian `BM` ARKit
  packets, text controls and `BP` JPEG previews over WebSocket/UDP port 9091.
  Its verified archive SHA256 is
  befd4c5189243d840810e535a55bd17ad848fe417d69af8fde271feeaf96e5f0.
- BlendMotion is a candidate sender; its iPhone app has not been run here.

## Validation boundaries

No GTA, Menyoo, iPhone or live Rockstar export has been tested. Headless tests
will cover packet rejection, coordinate conversion, calibration, packet loss,
track persistence and deterministic resampling. A successful build is not an
in-game acceptance result. Preview uses the visible GTA client window, refuses
other foreground applications, and must be checked for black/stale frames.

## Environment

Windows, no local MSVC or .NET SDK. Portable Zig/CMake/Ninja downloaded into the
chat's scratch directory for headless checks. Full ASI compilation uses MSVC
on a Windows CI runner. Local API DNS is misrouted; a per-process CONNECT
tunnel to a DNS-verified GitHub API address preserves normal TLS verification.

## Implemented (0.1.0 prototype)

- Pure track core with ARKit basis validation, yaw-aligned translation,
  quaternion rotation, adaptive smoothing and timestamp interpolation.
- Editor camera hook, loopback-only UDP, live/record/replay/recenter hotkeys,
  collision query fallback, tracking-loss freeze and asynchronous sidecars.
- Existing app protocol bridge: foreground GTA client preview, JPEG over
  WebSocket with UDP fallback, bounded settings and timeline/notifications.
- Camera time is stored relative to the current clip start. Completed takes
  are cached in memory so already-loaded clip switches do not wait for disk.
- Stock export is selected in shipped settings. Multi-clip projects require
  warming every saved track before export, as documented in the acceptance test.
- Russian installation, rollback and 10-second friend test instructions.
- MSVC Windows CI and reproducible package script including corresponding source.

## Checks performed before initial CI push

- Portable Zig C++20 build: 25 camera core checks passed.
- Python 3.12: 10 protocol/state tests and 1 real UDP/WebSocket integration
  test passed, including valid JPEG decoding by the simulated phone.
- `git diff --check` passed. Base checkout clean; no overlapping dirty files.
- universal-modder `um publish check .`: 91 files, 0 failures, one warning
  about inherited Ghidra-style labels in `src/lights/lightbuild.h`; these are
  upstream reverse-engineering reference comments, not distributed game files.
- GTA, real iPhone app and Rockstar export remain untested.

## Remaining acceptance

CI must compile the full ASI with MSVC. Friend must run the documented short
clip, reload and stock export, report game/iOS/app versions and compare camera
poses. No official mod release or game compatibility claim before that evidence.
