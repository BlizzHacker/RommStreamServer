#!/usr/bin/env python3
"""RomM Streaming Server.

Two play paths, one backend:
  - Roku (legacy):   Chromium+EmulatorJS or RetroArch on Xvfb → FFmpeg → HLS,
                     input relayed via CDP (Chromium) or xdotool (RetroArch).
  - Xbox (WebRTC):   RetroArch on Xvfb → aiortc H.264/Opus, input over the
                     WebRTC data channel. ~100 ms instead of HLS's 2-4 s.

Plus: tier routing (/api/play/route), ROM proxy for EmulatorJS-in-browser,
and save-state persistence shared across tiers.
"""

import asyncio
import json
import logging
import os
import urllib.parse
import uuid
from pathlib import Path

from aiohttp import web

import runner_retroarch
import saves
import tiers
from sessions import Allocator

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('romm-stream')

HLS_DIR = '/opt/romm-stream/hls'
ROM_BASE = '/mnt/usb1/roms'
PUBLIC_BASE = os.environ.get('ROMM_STREAM_PUBLIC', 'http://192.168.0.94:8091')

# Origins a web session (POST /api/stream/start {"url": ...}) may open.
WEB_SESSION_PREFIXES = (
    'https://crypticrealm.com',
    'https://www.crypticrealm.com',
    'https://worldofclaudecraft.com',
    'https://xbox.moveweight.com',
)

STREAMS = {}
ALLOC = Allocator()

PLAYER_HTML = '''<!DOCTYPE html><html><head><meta charset="utf-8">
<title>__NAME__</title>
<style>*{margin:0;padding:0}body{background:#000;overflow:hidden}</style>
</head><body>
<div id="game" style="width:1280px;height:720px"></div>
<script>
window.EJS_player = "#game";
window.EJS_core = "__CORE__";
window.EJS_gameUrl = "__ROM_URL__";
window.EJS_pathtodata = "/emu/data/";
window.EJS_startOnLoaded = true;
window.EJS_defaultOptions = {fullscreen: false};
</script>
<script src="/emu/data/loader.js"></script>
</body></html>'''

# CDP key map for web (Cryptic Realm) sessions: WASD movement + the game's
# default binds (E interact, Space jump, digits = ability slots, Tab target).
WEB_KEY_MAP = {
    'up': ('KeyW', 'w'), 'down': ('KeyS', 's'),
    'left': ('KeyA', 'a'), 'right': ('KeyD', 'd'),
    'a': ('KeyE', 'e'), 'b': ('Escape', 'Escape'),
    'x': ('Digit1', '1'), 'y': ('Digit2', '2'),
    'l1': ('Tab', 'Tab'), 'r1': ('Space', ' '),
    'start': ('KeyB', 'b'), 'select': ('Digit3', '3'),
}

# CDP key map for the legacy Chromium/EmulatorJS sessions.
KEY_MAP = {
    'up': 'ArrowUp', 'down': 'ArrowDown', 'left': 'ArrowLeft', 'right': 'ArrowRight',
    'a': 'KeyX', 'b': 'KeyZ', 'x': 'KeyS', 'y': 'KeyA',
    'l1': 'KeyQ', 'r1': 'KeyW', 'start': 'Enter', 'select': 'ShiftRight',
}


def resolve_rom(platform: str, rom_name: str) -> Path | None:
    """Path of a ROM under ROM_BASE; None if missing or traversal attempt."""
    if not platform or not rom_name:
        return None
    base = Path(ROM_BASE).resolve()
    try:
        p = (base / platform / rom_name).resolve()
    except (OSError, ValueError):
        return None
    if not str(p).startswith(str(base)) or not p.is_file():
        return None
    return p


# ---------------------------------------------------------------- tier route

async def handle_route(req):
    tier = tiers.route(req.query.get('platform', ''))
    if tier is None:
        return web.json_response({'error': 'unplayable'}, status=404)
    return web.json_response({'tier': tier})


# ---------------------------------------------------------------- ROM proxy

async def handle_romfile(req):
    p = resolve_rom(req.match_info['platform'],
                    urllib.parse.unquote(req.match_info['name']))
    if p is None:
        return web.json_response({'error': 'not found'}, status=404)
    return web.FileResponse(p, headers={
        'Access-Control-Allow-Origin': '*'})


# ---------------------------------------------------------------- saves

async def handle_save_put(req):
    try:
        p = saves.save_path(req.match_info['platform'],
                            urllib.parse.unquote(req.match_info['name']))
    except ValueError:
        return web.json_response({'error': 'bad path'}, status=400)
    body = await req.read()
    if len(body) > saves.MAX_SAVE_BYTES:
        return web.json_response({'error': 'too large'}, status=413)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(body)
    return web.json_response({'ok': True, 'bytes': len(body)})


async def handle_save_get(req):
    try:
        p = saves.save_path(req.match_info['platform'],
                            urllib.parse.unquote(req.match_info['name']))
    except ValueError:
        return web.json_response({'error': 'bad path'}, status=400)
    if not p.is_file():
        return web.json_response({'error': 'no save'}, status=404)
    return web.FileResponse(p, headers={'Access-Control-Allow-Origin': '*'})


# ------------------------------------------------- legacy HLS (Roku) session

async def start_ffmpeg_hls(display: str, stream_dir: Path):
    hls_path = str(stream_dir / 'stream.m3u8')
    seg = str(stream_dir / 'seg_%03d.ts')
    # Roku's H.264 decoder only handles up to High profile @ 4:2:0 (yuv420p).
    # x11grab captures RGB and libx264 would otherwise emit High 4:4:4 (yuv444p),
    # which Roku cannot decode -> the Video node buffers forever. Force yuv420p +
    # baseline-friendly profile/level, and add a silent AAC track (Roku HLS
    # dislikes video-only streams). 2s GOP with segment-aligned keyframes.
    return await asyncio.create_subprocess_exec(
        'ffmpeg',
        '-f', 'x11grab', '-video_size', '1280x720',
        '-framerate', '30', '-i', display + '.0+0,0',
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
        '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
        '-profile:v', 'high', '-level', '4.0', '-pix_fmt', 'yuv420p',
        '-b:v', '3M', '-maxrate', '3M', '-bufsize', '6M',
        '-g', '60', '-keyint_min', '60', '-sc_threshold', '0',
        '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
        '-hls_time', '2', '-hls_list_size', '6',
        '-hls_flags', 'delete_segments+independent_segments',
        # Roku's HLS player expects a MASTER playlist with #EXT-X-STREAM-INF,
        # not a bare media playlist; without it playback fails with a vague
        # "error in the HTTP response". Emit master.m3u8 alongside the media list.
        '-master_pl_name', 'master.m3u8',
        '-hls_segment_filename', seg, '-f', 'hls', hls_path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)


async def handle_start(req):
    """Roku-compatible HLS session. Uses RetroArch when the platform has a
    server core (better compat), else the legacy Chromium+EmulatorJS page."""
    try:
        data = await req.json()
    except Exception:
        data = {}
    platform = data.get('platform', 'n64')
    rom_name = data.get('rom_name', '')
    web_url = data.get('url', '')
    display_name = data.get('name', rom_name or 'game')

    rom = None
    if web_url:
        # Web session (e.g. Cryptic Realm on Roku): headless Chromium runs the
        # page itself. Allowlisted origins only — this must not be an open proxy.
        if not any(web_url.startswith(p) for p in WEB_SESSION_PREFIXES):
            return web.json_response({'error': 'url not allowed'}, status=403)
    else:
        rom = resolve_rom(platform, rom_name)
        if rom is None:
            return web.json_response({'error': 'rom not found'}, status=404)

    try:
        display_num, debug_port = ALLOC.acquire()
    except RuntimeError:
        return web.json_response({'error': 'server busy'}, status=503)

    sid = uuid.uuid4().hex[:8]
    stream_dir = Path(HLS_DIR) / sid
    stream_dir.mkdir(parents=True, exist_ok=True)
    display = f':{display_num}'

    xvfb = await runner_retroarch.start_xvfb(display_num)
    engine, chrome, ra, cdp_ws = 'retroarch', None, None, ''

    if rom is not None and tiers.stream_core(platform):
        try:
            ra = await runner_retroarch.start_retroarch(
                platform, str(rom), display_num, rom_name)
        except FileNotFoundError:
            ra = None
    if ra is None:
        engine = 'chromium'
        if web_url:
            page_url = web_url
        else:
            core = tiers.EJS_CORES.get(platform, platform)
            rom_url = f'{PUBLIC_BASE}/roms/' + urllib.parse.quote(
                platform + '/' + rom_name)
            html = (PLAYER_HTML.replace('__NAME__', display_name)
                    .replace('__CORE__', core).replace('__ROM_URL__', rom_url))
            (stream_dir / 'player.html').write_text(html)
            page_url = f'{PUBLIC_BASE}/player/{sid}/player.html'
        chrome = await asyncio.create_subprocess_exec(
            'chromium', '--display=' + display, '--no-sandbox',
            '--disable-gpu-sandbox', '--use-gl=angle',
            '--use-angle=swiftshader', '--enable-webgl',
            '--ignore-gpu-blocklist', '--window-size=1280,720',
            '--start-fullscreen',
            '--remote-debugging-port=' + str(debug_port),
            # Chromium >=111 rejects CDP WebSocket connections (403) unless the
            # origin is allowlisted; without this every controller keypress
            # fails silently at handle_input's websocket.create_connection.
            '--remote-allow-origins=*',
            page_url,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            env=dict(os.environ, DISPLAY=display))
        await asyncio.sleep(4)
        try:
            from urllib.request import urlopen
            pages = json.loads(urlopen(
                f'http://localhost:{debug_port}/json', timeout=5).read())
            if pages:
                cdp_ws = pages[0].get('webSocketDebuggerUrl', '')
        except Exception:
            pass

    ffmpeg = await start_ffmpeg_hls(display, stream_dir)
    # Wait until the master playlist AND at least one segment exist before
    # returning, otherwise the client (Roku) requests master.m3u8 during the
    # FFmpeg startup window, gets 404, and aborts playback. Up to ~12s.
    master_pl = stream_dir / 'master.m3u8'
    for _ in range(60):
        segs = list(stream_dir.glob('seg_*.ts'))
        if master_pl.exists() and len(segs) >= 2:
            break
        await asyncio.sleep(0.2)

    STREAMS[sid] = {'xvfb': xvfb, 'chrome': chrome, 'retroarch': ra,
                    'ffmpeg': ffmpeg, 'display_num': display_num,
                    'engine': engine, 'rom_name': display_name,
                    'cdp_ws': cdp_ws, 'web': bool(web_url)}
    # Point clients at the master playlist (Roku requires #EXT-X-STREAM-INF).
    # RetroArch/HLS-only sessions without a master fall back to the media list.
    return web.json_response({
        'stream_id': sid, 'engine': engine,
        'hls_url': f'{PUBLIC_BASE}/hls/{sid}/master.m3u8',
        'debug_port': debug_port})


async def handle_stop(req):
    s = STREAMS.pop(req.match_info['sid'], None)
    if s:
        await runner_retroarch.terminate(
            s.get('ffmpeg'), s.get('chrome'), s.get('retroarch'), s.get('xvfb'))
        ALLOC.release(s['display_num'])
    return web.json_response({'ok': True})


async def handle_input(req):
    sid = req.match_info['sid']
    try:
        data = await req.json()
    except Exception:
        data = {}
    key, pressed = data.get('key', ''), data.get('pressed', True)
    s = STREAMS.get(sid)
    if not s:
        return web.json_response({'error': 'stream not found'}, status=404)

    if s['engine'] == 'retroarch':
        ok = await runner_retroarch.send_key(s['display_num'], key, pressed)
        return web.json_response({'ok': ok, 'key': key})

    if s.get('web'):
        code, char = WEB_KEY_MAP.get(key, (key, key))
    else:
        code = char = KEY_MAP.get(key, key)
    try:
        import websocket
        ws_url = s.get('cdp_ws', '').replace('localhost', '127.0.0.1')
        if ws_url:
            page_ws = websocket.create_connection(ws_url, timeout=2)
            page_ws.send(json.dumps({'id': 1, 'method': 'Input.dispatchKeyEvent',
                                     'params': {'type': 'keyDown' if pressed else 'keyUp',
                                                'key': char, 'code': code,
                                                'windowsVirtualKeyCode': 0}}))
            page_ws.close()
    except Exception:
        pass
    return web.json_response({'ok': True, 'key': key, 'mapped': code})


async def handle_status(req):
    return web.json_response({'streams': [
        {'id': k, 'name': v['rom_name'], 'engine': v['engine']}
        for k, v in STREAMS.items()]})


# ------------------------------------------------------ WebRTC (Xbox) session

async def handle_rtc_signal(req):
    """WS signaling + full session lifecycle for one WebRTC play session."""
    platform = req.query.get('platform', '')
    rom_name = urllib.parse.unquote(req.query.get('rom_name', ''))
    rom = resolve_rom(platform, rom_name)
    if rom is None or not tiers.stream_core(platform):
        return web.json_response({'error': 'not streamable'}, status=404)

    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(req)

    try:
        display_num, _ = ALLOC.acquire()
    except RuntimeError:
        await ws.send_json({'type': 'error', 'error': 'server busy'})
        await ws.close()
        return ws

    xvfb = ra = None
    try:
        xvfb = await runner_retroarch.start_xvfb(display_num)
        # per-session pulse null sink for audio capture
        await (await asyncio.create_subprocess_exec(
            'pactl', 'load-module', 'module-null-sink',
            f'sink_name=romm{display_num}',
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL)).wait()
        ra = await runner_retroarch.start_retroarch(
            platform, str(rom), display_num, rom_name)
        await ws.send_json({'type': 'ready'})

        import webrtc

        async def cleanup():
            await runner_retroarch.terminate(ra, xvfb)
            ALLOC.release(display_num)

        await webrtc.run_peer(ws, display_num, cleanup)
    except Exception as e:
        log.exception('rtc session failed')
        try:
            await ws.send_json({'type': 'error', 'error': str(e)})
        except Exception:
            pass
        await runner_retroarch.terminate(ra, xvfb)
        ALLOC.release(display_num)
    finally:
        if not ws.closed:
            await ws.close()
    return ws


# --------------------------------------------------------------------- app

def build_app() -> web.Application:
    app = web.Application(client_max_size=saves.MAX_SAVE_BYTES + 1024)
    r = app.router
    r.add_post('/api/stream/start', handle_start)
    r.add_post('/api/stream/{sid}/stop', handle_stop)
    r.add_post('/api/stream/{sid}/input', handle_input)
    r.add_get('/api/stream/status', handle_status)
    r.add_get('/api/play/route', handle_route)
    r.add_get('/api/romfile/{platform}/{name}', handle_romfile)
    r.add_put('/api/saves/{platform}/{name}', handle_save_put)
    r.add_get('/api/saves/{platform}/{name}', handle_save_get)
    r.add_get('/api/rtc/signal', handle_rtc_signal)
    return app


async def main():
    runner = web.AppRunner(build_app())
    await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8090).start()
    log.info('RomM stream server on :8090')
    await asyncio.Future()


if __name__ == '__main__':
    asyncio.run(main())
