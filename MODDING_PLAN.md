# GTA iPhone camera modding plan

Target: GTA V Legacy Story Mode, iPhone 12 Pro Max, Menyoo, Rockstar Editor.
Actual game install path, game build, iOS version and installed loader versions
are unknown and will be captured during the friend's first test.

## Route

- REPlus fork (GPL-3.0) uses its existing per-build camera signatures.
- BlendMotion from the App Store supplies ARKit world tracking; Blender is
  not part of the proposed runtime.
- A Windows bridge receives tracking and forwards a validated pose to a
  loopback-only ASI receiver. The same bridge sends a GTA preview to iPhone.
- A separate per-project/per-clip camera track survives game restart and is
  evaluated by replay time during scrubbing, preview and stock export.
- No SteamVR, GTA: Vive, spawned camera characters or moving physics bodies.

## First acceptance slice

1. Record a short scene normally and open it in a named Rockstar project.
2. Record one phone camera take while the clip plays.
3. Disconnect the phone, scrub the clip and export it.
4. Restart GTA, open the saved project and export the same movement again.

This proves the critical workflow before calling the prototype game-verified.

## Installation and recovery

The friend backs up saves and existing REPlus files before installation.
Do not overwrite any existing ASI/INI silently. Ship only the custom mod,
bridge, GPL source, instructions and diagnostics, never GTA or third-party
commercial application files. Only offline Story Mode is in scope.

## Known constraints

- Native editor offsets/signatures require real-game validation.
- Preview needs a visible, foreground GTA window on the local PC; the first
  version sends the client view including editor overlays.
- Recorded phone tracks need this fork installed for replay/export. Keep the
  sidecar folder with the project and back it up.
- World-space camera tracks are not vehicle mounts. Collision avoidance and
  tracking drift must be judged in the target scene.
- Existing-app region availability and iOS compatibility must be checked by
  the friend before purchase; no purchase is required for headless tests.
