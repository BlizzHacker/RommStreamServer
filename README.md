# RomM Stream Server

> A **[Cartridge](https://github.com/BlizzHacker/rom-hub/blob/master/BRAND.md)** app by MoveWeight — the play pillar. Cartridge is a self-hosted retro-gaming ecosystem. Unofficial; not affiliated with RomM, Gaseous or Retrom.

The shared game-play backend for a [RomM](https://github.com/rommapp/romm)
library: it runs the emulator server-side and delivers the picture to devices
that can't emulate for themselves. A Roku gets a low-latency HLS stream with
your phone as the gamepad; an Xbox gets ~100 ms WebRTC with the real
controller. One server, both paths:

```
                    ┌────────────────────────────────────────────┐
   Roku channel ───►│  HLS: Xvfb + RetroArch (or Chromium+EJS)   │
   (RommForRoku)    │       → FFmpeg x264 → .m3u8/.ts            │
                    │  input: phone PWA → CDP / xdotool          │
                    │                                            │
   Xbox Edge   ───►│  WebRTC: Xvfb + RetroArch → aiortc H.264    │
   (RommForXbox)    │       ~100 ms; input over data channel     │
                    └────────────────────────────────────────────┘
```

Client apps that use it: [RommForRoku](https://github.com/BlizzHacker/RommForRoku),
[RommForXbox](https://github.com/BlizzHacker/RommForXbox),
[RommForDesktop](https://github.com/BlizzHacker/RommForDesktop) (as its
fallback for platforms with no local core).

## Deploy

On the RomM host (Debian):

```bash
bash deploy/deploy.sh
```

That installs RetroArch, xdotool, Xvfb, pulseaudio, a python venv with
aiohttp/aiortc, downloads libretro cores (dolphin, flycast, mupen64plus-next,
ppsspp, mednafen-saturn, mednafen-psx-hw, fbneo, pcsx2, citra) from the
libretro buildbot, and installs the nginx site
([deploy/nginx-xbox.conf](deploy/nginx-xbox.conf)) and the systemd unit.

Ports: `8090` API · `8091` legacy nginx (HLS/phone/roms) · `8092` Xbox front
(same-origin `/romm/` + `/api/` proxies).

## What it provides

- **Tier routing** — `GET /api/play/route?platform=slug` → `local`
  (client runs EmulatorJS itself) / `stream` (server RetroArch) / 404
  (denylisted: Windows, installers, mobile). Source of truth: [tiers.py](tiers.py).
- **HLS sessions** (Roku-compatible):
  `POST /api/stream/start` `{platform, rom_name, name, client}` →
  `{stream_id, hls_url, engine}`; `POST /api/stream/{sid}/stop`;
  `POST /api/stream/{sid}/input` `{key, pressed}`; `GET /api/stream/status`.
  Uses RetroArch cores when available, headless-Chromium EmulatorJS otherwise.
- **WebRTC sessions** — `GET /api/rtc/signal?platform=&rom_name=` (WebSocket):
  server sends `{type:"ready"}`, client sends an SDP offer, server answers with
  x11grab video + pulse audio; the `input` data channel carries
  `{key, pressed}` injected via xdotool.
- **ROM proxy** — `GET /api/romfile/{platform}/{name}` (traversal-safe) for
  in-browser EmulatorJS clients.
- **Save persistence** — `PUT/GET /api/saves/{platform}/{rom_name}` +
  per-session RetroArch save dirs, shared across tiers and devices — start on
  the Xbox, continue on the Roku.
- **Phone controller** — [phone.html](phone.html), a PWA gamepad for HLS
  sessions.

## Tests

```
python -m pytest tests -q
```

Covers tier routing, the display/port allocator (no reuse while held),
save-path traversal rejection, and the HTTP endpoints via aiohttp's test client.
