# RommForXbox + RommStreamServer evolution — Design

Date: 2026-07-11 · Approved by user in chat.

## Goal

Play the entire RomM library (CT104, 192.168.0.94, RomM 4.9.2) on **any retail Xbox** —
no dev mode — via the Xbox Edge browser. Everything except Windows/installer (`.exe`)
platforms must be playable. Roku keeps working and benefits from the shared backend.

## Architecture

One URL in Xbox Edge: `http://192.168.0.94:8092` (later `xbox.moveweight.com`).
Served by nginx on CT104 next to RomM.

### Component 1 — RommForXbox web client (new repo `BlizzHacker/RommForXbox`)

Static single-page app, no build step (plain HTML/JS/CSS — matches existing repo style).

- **Pairing**: RomM 4.9 client-API-token 8-digit pairing flow (same model as Roku,
  never sees a password). Token in `localStorage`; read-only `platforms.read`,
  `roms.read` (+ `assets.read` for box art).
- **Library UI**: 10-foot design. Platform rail + box-art tile grid from
  `GET /api/platforms`, `GET /api/roms?platform_id=`. Full Gamepad API navigation
  (D-pad/left-stick move, A select, B back, LB/RB platform switch, Y search).
  Also mouse/keyboard usable so it works in any browser for testing.
- **Platform filter**: hide Windows/installer platforms (win, win3x, dos-with-exe
  installers, android, etc. — a denylist + "has playable core in tier map" check).
- **Play flow**: client asks server `GET /api/play/route?platform=` → `{tier: "local"|"stream"}`.
  - **Tier 1 local**: loads EmulatorJS in-page (`/emu/data/` already on CT104),
    ROM via authenticated proxy `/api/romfile/{rom_id}` (server fetches from RomM
    with its own token or streams from `/mnt/usb1/roms`). Gamepad works natively in EJS.
  - **Tier 2 stream**: opens `<video>` fed by WebRTC from the server; controller
    state sampled with Gamepad API at 60 Hz and sent over the WebRTC data channel.
- **In-game overlay**: hold Start+Select (Menu+View) 1 s → menu: resume / save state /
  load state / quit to library.

### Component 2 — RommStreamServer evolution (shared backend)

`server.py` grows from Roku-only HLS launcher into the shared backend:

- **Session manager**: replaces ad-hoc `STREAMS` dict; per-session runner is either
  the legacy Chromium+EJS+HLS path (Roku) or the new RetroArch+WebRTC path (Xbox).
  Fixes existing display-number/port collision bug (`90 + len(STREAMS)` reuses
  numbers after stops) with a proper allocator.
- **RetroArch runner**: headless RetroArch (Xvfb) with libretro cores for heavy
  systems: GameCube/Wii (dolphin), Dreamcast (flycast), PS2 (pcsx2/play!),
  Saturn (beetle-saturn), N64 hi-fidelity (mupen64plus-next), PSP (ppsspp).
  Installed on CT104 via apt/libretro buildbot cores.
- **WebRTC out**: GStreamer `webrtcbin` (or aiortc) pipeline — ximagesrc → x264
  ultrafast zerolatency → webrtcbin; audio via pulse null-sink → opus. Signaling
  over a WebSocket on the existing aiohttp app. Uses the coturn TURN server already
  running on CT104 for ICE (LAN will go host-direct anyway).
- **Input**: WebRTC data-channel messages `{btn, pressed}` / axis values → injected
  via `xdotool`/uinput into the RetroArch X display. (CDP path stays for legacy EJS
  sessions.)
- **Tier map**: single source of truth `TIER_MAP = {platform_slug: "local"|"stream"|"both"}`
  exposed at `/api/play/route`. Slugs not in map and denylisted (windows/exe) are
  unplayable → UI hides them.
- **Save persistence**: RetroArch save/state dirs per game under
  `/opt/romm-stream/saves/{platform}/{rom}`; EJS tier gets save-state upload/download
  endpoints (`PUT/GET /api/saves/{rom_id}`) storing in the same tree.
- **HLS endpoint stays** for Roku, unchanged API, but Roku sessions may use the
  RetroArch runner (better compatibility) with FFmpeg x11grab → HLS as today.

### Component 3 — RommForRoku alignment

- Review outcome: repo is healthy post-#1 merge; keep. Changes: point at evolved
  endpoints (start payload gains `client: "roku"`), version bump; no architectural
  change (Roku physically can't do WebRTC/EJS).

## Data flow (Tier 2)

Xbox Edge → WS `/api/rtc/signal` → server spawns Xvfb+RetroArch+GStreamer →
SDP/ICE exchange (coturn) → video/audio track to Xbox `<video>`, input via data
channel → xdotool keys into RetroArch. Stop = session teardown, saves flushed.

## Error handling

- Pairing token invalid/expired → re-pair screen.
- Tier 2 session limit (default 2 concurrent) → friendly "server busy" toast.
- WebRTC connect fail → automatic fallback to HLS `<video>` (Edge plays HLS via hls.js).
- Server restarts reap orphan Xvfb/RetroArch/GStreamer PIDs on boot.

## Testing

- Unit: tier routing, session allocator, save path mapping (pytest, no X needed).
- Integration on CT104: start/stop stream session headless, assert RTP flowing
  (GStreamer stats) and HLS fallback file appears.
- Manual: Xbox Edge — pair, browse, play SNES (tier 1) and GameCube (tier 2),
  verify controller + save states.

## Deploy

CT104: `/opt/romm-stream` (server + web app under `/opt/romm-stream/xbox/`),
nginx :8091 gains `/xbox/` + `/api/rtc` routes, new listen :8092 → xbox app root.
systemd unit `romm-stream.service` (currently server runs unmanaged — add unit).

## Out of scope

Real Xbox-exe/UWP native app; Windows-game streaming (Sunshine/Moonlight class);
internet-facing exposure hardening beyond token auth (LAN-first, Traefik later).
