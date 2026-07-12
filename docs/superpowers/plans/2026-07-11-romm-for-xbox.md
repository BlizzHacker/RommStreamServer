# RommForXbox + Stream Server Evolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Any retail Xbox plays the whole RomM library via Edge: EmulatorJS in-browser (tier 1) + server-side RetroArch over WebRTC (tier 2).

**Architecture:** Static gamepad-first SPA (new repo RommForXbox) served by nginx on CT104; RommStreamServer's aiohttp `server.py` evolves into a session manager with two runners (legacy Chromium+HLS for Roku, RetroArch+aiortc WebRTC for Xbox), tier routing, and save persistence.

**Tech Stack:** Python 3 aiohttp + aiortc (WebRTC, x11grab via PyAV), Xvfb, RetroArch + libretro cores, xdotool, EmulatorJS (already at /opt/romm-stream/emulatorjs), plain HTML/JS/CSS client, nginx, systemd.

## Global Constraints

- Targets RomM 4.9.2 API on http://192.168.0.94:8080 (client API-token pairing; read-only scopes).
- Xbox client must run in Xbox Edge: no build step, Gamepad API, no dev mode.
- Roku HLS API endpoints stay backward compatible (`POST /api/stream/start`, `/stop`, `/input`, `/status`).
- ROMs at `/mnt/usb1/roms/{platform_slug}/`; saves under `/opt/romm-stream/saves/`.
- Windows/installer platforms are never playable/visible.
- Deploy target: CT104 via `ssh root@192.168.0.6 pct exec 104`.

---

### Task 1: Server core refactor — session allocator, tier map, routing endpoint

**Files:** Modify `server.py` (split into `server.py` + `sessions.py` + `tiers.py`); Create `tests/test_tiers.py`, `tests/test_sessions.py`.

**Interfaces produced:**
- `tiers.route(platform_slug) -> "local"|"stream"|None`; `tiers.TIER_MAP`, `tiers.DENYLIST`
- `sessions.Allocator().acquire() -> (display_num, debug_port)` / `.release(display_num)` — no reuse while held
- HTTP `GET /api/play/route?platform=slug` → `{"tier": "local"}` or 404 `{"error":"unplayable"}`

- [ ] Write failing pytest for tier routing (local: snes/nes/gba/psx…; stream: ngc/wii/dc/ps2; None: win/dos/android) and allocator no-collision after release/acquire cycles.
- [ ] Implement `tiers.py` (TIER_MAP covering EJS cores as "local", heavy cores as "stream", denylist) and `sessions.py` allocator; wire `/api/play/route`.
- [ ] Run pytest → PASS. Commit `feat: tier routing + session allocator`.

### Task 2: RetroArch + WebRTC runner (server)

**Files:** Create `runner_retroarch.py`, `webrtc.py`; Modify `server.py`.

**Interfaces produced:**
- `runner_retroarch.start(platform, rom_path, display) -> proc` (Xvfb display assumed up; RetroArch fullscreen with per-platform libretro core from `CORE_PATHS`)
- WS `GET /api/rtc/signal?platform=&rom_id=` — JSON messages `{type: offer/answer/ice}`; server side builds `RTCPeerConnection` with video track from `MediaPlayer(display, format="x11grab", options={framerate:"30", video_size:"1280x720"})`, audio from pulse sink; data channel `input` receives `{"key": "a", "pressed": true}` → `xdotool key --window <ra>` mapping to RetroArch default keyboard binds.
- Fallback: every stream session also writes HLS (reuse existing ffmpeg pipeline) so Roku and WebRTC-failure clients share it.

- [ ] Implement runner + signaling; input map for RetroArch defaults (x/z/s/a/q/w/enter/rshift/arrows) via xdotool keydown/keyup.
- [ ] Unit test: core path selection + input key mapping (pure functions).
- [ ] Commit `feat: RetroArch runner + aiortc WebRTC signaling`.

### Task 3: ROM proxy + save persistence endpoints

**Files:** Modify `server.py`; Create `saves.py`, `tests/test_saves.py`.

**Interfaces produced:**
- `GET /api/romfile/{platform}/{name}` → streams from `/mnt/usb1/roms` (path-traversal-safe).
- `PUT/GET /api/saves/{platform}/{rom_name}` (binary body ≤ 16 MB) → `/opt/romm-stream/saves/{platform}/{rom_name}.state`.
- RetroArch launched with `--savefile/--savestate` dirs in the same tree.

- [ ] TDD save path mapping incl. traversal rejection (`..`, absolute) → implement → PASS → commit.

### Task 4: RommForXbox web client (new repo)

**Files:** Create in `C:\MoveWeight\romm\RommForXbox`: `index.html`, `app.js`, `gamepad.js`, `rtc.js`, `style.css`, `README.md`.

**Interfaces consumed:** RomM API (`/api/platforms`, `/api/roms?platform_id=&limit=&offset=`, `/api/login/pair` flow as in Roku channel RommTask.brs), server `/api/play/route`, `/api/romfile/...`, `/api/rtc/signal`, `/api/saves/...`, `/emu/data/loader.js`.

- Pairing screen (8-digit code entry, on-screen number row, stores token in localStorage; Authorization: Bearer on RomM calls).
- Tile grid: platform rail (LB/RB), cover art from RomM assets endpoint, D-pad/stick focus movement with scroll; A=play, B=back, Y=search overlay.
- Tier local: inject EJS config (same CORE_MAP as server) + `EJS_gameUrl=/api/romfile/...`; on exit, offer save-state upload.
- Tier stream: fullscreen `<video autoplay playsinline>`; `rtc.js` does offer/answer over WS; `gamepad.js` samples `navigator.getGamepads()` at 60 Hz, sends edges on data channel; Menu+View hold 1s = quit overlay.
- Denylist/unroutable platforms filtered out of the rail.

- [ ] Build client; verify in desktop Edge with an Xbox controller; commit; create GitHub repo `BlizzHacker/RommForXbox` and push.

### Task 5: Deploy to CT104

- [ ] Install: `apt install retroarch xdotool pulseaudio xvfb python3-aiohttp; pip install aiortc av websocket-client` (or venv); fetch libretro cores (dolphin/flycast/mupen64plus-next/ppsspp/pcsx2 via libretro buildbot x86_64) into `/opt/romm-stream/cores/`.
- [ ] Copy evolved server + xbox client to `/opt/romm-stream/`; extend nginx: `/xbox/` root, `/api/` proxy incl. WS upgrade for `/api/rtc/signal`, listen 8092 → xbox app; `systemd romm-stream.service`; restart, smoke: `curl /api/play/route?platform=snes` → local; start a stream session headless and assert `stream.m3u8` grows.
- [ ] Commit deploy scripts (`scripts/deploy.sh`) to RommStreamServer.

### Task 6: Roku alignment + docs + push

- [ ] RommForRoku: add `client: "roku"` to start payload, README note about shared backend; bump manifest version; commit+push.
- [ ] RommStreamServer README rewrite (two-tier architecture diagram); push both repos.

## Self-review

Spec coverage: pairing(T4), library UI(T4), tier1(T4), tier2(T2/T4), tier map(T1), saves(T3), HLS-compat(T2/global), deploy(T5), Roku(T6) — covered. Types consistent across tasks (route() slugs = RomM platform slugs everywhere).
