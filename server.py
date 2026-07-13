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
    'https://romm.moveweight.com',
    'http://localhost:8080',   # RomM served locally on the same host
    'http://127.0.0.1:8080',
)

# RomM instance this server drives for autoplay (login + EJS launcher). Any
# RomM base can be passed per-request as romm_base for "anyone's RomM server".
ROMM_LOCAL_BASE = os.environ.get('ROMM_BASE', 'http://localhost:8080')

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
window.EJS_volume = 0.5;
window.EJS_defaultOptions = { fullscreen: false };
</script>
<script src="/emu/data/loader.js"></script>
</body></html>'''

# CDP key map for web (Cryptic Realm) sessions: WASD movement + the game's
# default binds (E interact, Space jump, digits = ability slots, Tab target).
# Cryptic Realm (web game) key map: WASD movement + game binds.
WEB_KEY_MAP = {
    'up': ('KeyW', 'w'), 'down': ('KeyS', 's'),
    'left': ('KeyA', 'a'), 'right': ('KeyD', 'd'),
    'a': ('KeyE', 'e'), 'b': ('Escape', 'Escape'),
    'x': ('Digit1', '1'), 'y': ('Digit2', '2'),
    'l1': ('Tab', 'Tab'), 'r1': ('Space', ' '),
    'start': ('KeyB', 'b'), 'select': ('Digit3', '3'),
    'enter': ('Enter', 'Enter'),
}

# EmulatorJS default keyboard binds (RomM games): arrows + z/x/a/s/enter/shift.
EJS_KEY_MAP = {
    'up': ('ArrowUp', 'ArrowUp'), 'down': ('ArrowDown', 'ArrowDown'),
    'left': ('ArrowLeft', 'ArrowLeft'), 'right': ('ArrowRight', 'ArrowRight'),
    'a': ('KeyX', 'x'), 'b': ('KeyZ', 'z'),
    'x': ('KeyS', 's'), 'y': ('KeyA', 'a'),
    'l1': ('KeyQ', 'q'), 'r1': ('KeyW', 'w'),
    'start': ('Enter', 'Enter'), 'select': ('ShiftRight', 'Shift'),
    'enter': ('Enter', 'Enter'),
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


async def _cdp_eval(ws, expr, await_promise=False):
    """Evaluate JS in the page over an open CDP websocket; return the value."""
    import websocket as _ws
    _id = 1
    ws.send(json.dumps({'id': _id, 'method': 'Runtime.evaluate', 'params': {
        'expression': expr, 'returnByValue': True,
        'awaitPromise': await_promise}}))
    while True:
        m = json.loads(ws.recv())
        if m.get('id') == _id:
            return m.get('result', {}).get('result', {}).get('value')


async def romm_autoplay(cdp_ws, base, rom_id, user, password):
    """Drive RomM's web UI: login if on the login page, then click EJS Play."""
    import websocket
    try:
        ws = websocket.create_connection(
            cdp_ws.replace('localhost', '127.0.0.1'), timeout=8)
    except Exception:
        return

    # Turn off Chrome's password-save UI at the DevTools level so no bubble
    # appears after the login form submits.
    try:
        for m in ('Autofill.disable', 'Page.enable'):
            ws.send(json.dumps({'id': 50, 'method': m, 'params': {}}))
            while True:
                r = json.loads(ws.recv())
                if r.get('id') == 50:
                    break
    except Exception:
        pass

    def ev(expr):
        ws.send(json.dumps({'id': 1, 'method': 'Runtime.evaluate', 'params': {
            'expression': expr, 'returnByValue': True}}))
        while True:
            m = json.loads(ws.recv())
            if m.get('id') == 1:
                return m.get('result', {}).get('result', {}).get('value')

    try:
        # If we landed on the login page, fill + submit.
        for _ in range(20):
            url = ev('location.href') or ''
            if '/login' in url and user:
                fill = ('(function(){function s(el,v){var d=Object.'
                        'getOwnPropertyDescriptor(HTMLInputElement.prototype,'
                        '"value").set;d.call(el,v);el.dispatchEvent(new Event('
                        '"input",{bubbles:true}));el.dispatchEvent(new Event('
                        '"change",{bubbles:true}));}var u=document.querySelector('
                        '"input[name=username],input[type=text]");var p='
                        'document.querySelector("input[type=password]");'
                        'if(u&&p){s(u,%r);s(p,%r);var b=[...document.'
                        'querySelectorAll("button")].find(b=>/login/i.test('
                        'b.innerText)&&!/authentik/i.test(b.innerText));'
                        'if(b){b.click();return "submitted";}}return "waiting";'
                        '})()') % (user, password)
                ev(fill)
                await asyncio.sleep(4)
            elif ('/rom/' in url) or (user == ''):
                break
            else:
                await asyncio.sleep(0.5)
        # Dismiss Chrome's "save password?" bubble (browser UI, not DOM) by
        # sending Escape via CDP so it can't cover the Play button.
        try:
            for t in ('keyDown', 'keyUp'):
                ws.send(json.dumps({'id': 9, 'method': 'Input.dispatchKeyEvent',
                    'params': {'type': t, 'key': 'Escape', 'code': 'Escape',
                               'windowsVirtualKeyCode': 27}}))
                while True:
                    m = json.loads(ws.recv())
                    if m.get('id') == 9:
                        break
        except Exception:
            pass
        # Ensure we're on the EJS launcher, then click Play.
        cur = ev('location.href') or ''
        if f'/rom/{rom_id}/ejs' not in cur:
            ev('window.location.assign(%r)' % f'{base}/rom/{rom_id}/ejs')
        for _ in range(30):
            r = ev('(function(){var b=[...document.querySelectorAll("button")]'
                   '.find(b=>b.innerText.trim()==="Play");if(b){b.click();'
                   'return "play";}return "wait";})()')
            if r == 'play':
                break
            await asyncio.sleep(0.5)
    except Exception:
        pass
    finally:
        try:
            ws.close()
        except Exception:
            pass


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
    client = data.get('client', '')
    display_name = data.get('name', rom_name or 'game')
    # RomM autoplay: drive RomM's own web UI (login -> EJS launcher -> Play) in
    # Chromium, exactly like a real user, so RomM configures the emulator. Far
    # more reliable than launching a bare EJS core headless.
    romm_rom_id = data.get('romm_rom_id')
    romm_base = data.get('romm_base', ROMM_LOCAL_BASE)
    romm_user = data.get('romm_user', '')
    romm_pass = data.get('romm_pass', '')
    if romm_rom_id:
        web_url = f'{romm_base}/rom/{romm_rom_id}/ejs'

    # Reap prior sessions from the same client (e.g. a Roku relaunch) so they
    # don't pile up as orphan Chromium/FFmpeg processes and confuse which
    # session is live. A client only ever needs one active stream.
    if client:
        for old_sid in [k for k, v in STREAMS.items() if v.get('client') == client]:
            old = STREAMS.pop(old_sid, None)
            if old:
                await runner_retroarch.terminate(
                    old.get('ffmpeg'), old.get('chrome'),
                    old.get('retroarch'), old.get('xvfb'))
                ALLOC.release(old['display_num'])

    rom = None
    if web_url:
        # Web session (e.g. Cryptic Realm on Roku): headless Chromium runs the
        # page itself. Allowlisted origins only — this must not be an open proxy.
        # RomM autoplay (romm_rom_id set) may target any RomM base the caller
        # provides, since it requires valid RomM credentials to do anything.
        if not romm_rom_id and not any(
                web_url.startswith(p) for p in WEB_SESSION_PREFIXES):
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

    # Prefer EmulatorJS-in-Chromium (SwiftShader software WebGL) whenever the
    # platform has an EJS core: headless RetroArch's native GL context hangs on
    # this GPU-less box. Only use RetroArch for heavy systems EJS can't do
    # (GameCube/Wii/PS2/Saturn/3DS), which have a stream_core but no EJS_CORES.
    ejs_capable = platform in tiers.EJS_CORES
    if rom is not None and tiers.stream_core(platform) and not ejs_capable:
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
        # Fresh throwaway profile per session + crash flags kill the
        # "Chromium didn't shut down correctly / Restore pages?" infobar that
        # appears when a prior session's profile was left dirty.
        profile_dir = str(stream_dir / 'chrome-profile')
        chrome = await asyncio.create_subprocess_exec(
            'chromium', '--display=' + display, '--no-sandbox',
            '--disable-gpu-sandbox', '--use-gl=angle',
            '--use-angle=swiftshader', '--enable-webgl',
            '--ignore-gpu-blocklist', '--window-size=1280,720',
            '--start-fullscreen', '--kiosk',
            '--user-data-dir=' + profile_dir,
            '--no-first-run', '--no-default-browser-check',
            '--disable-session-crashed-bubble',
            '--disable-infobars', '--hide-crash-restore-bubble',
            '--disable-features=InfiniteSessionRestore,Translate,'
            'PasswordManagerOnboarding,AutofillEnableAccountWalletStorage,'
            'PasswordManager,PasswordGeneration,AutofillServerCommunication',
            '--disable-save-password-bubble',
            '--password-store=basic',
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

        # RomM autoplay: log in (if creds given) then click the EJS "Play"
        # button, so the game boots without any user gesture.
        if romm_rom_id and cdp_ws:
            await romm_autoplay(cdp_ws, romm_base, romm_rom_id,
                                romm_user, romm_pass)

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
                    'cdp_ws': cdp_ws, 'web': bool(web_url), 'client': client,
                    'ejs': bool(romm_rom_id)}
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

    if s.get('ejs'):
        code, char = EJS_KEY_MAP.get(key, (key, key))
    elif s.get('web'):
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
        {'id': k, 'name': v['rom_name'], 'engine': v['engine'],
         'client': v.get('client', '')}
        for k, v in STREAMS.items()]})


def _cdp_send(cdp_ws, method, params):
    """Fire a single CDP command over a short-lived websocket."""
    import websocket
    try:
        ws = websocket.create_connection(
            cdp_ws.replace('localhost', '127.0.0.1'), timeout=2)
        ws.send(json.dumps({'id': 1, 'method': method, 'params': params}))
        ws.close()
        return True
    except Exception:
        return False


async def handle_text(req):
    """Type a whole string into the focused field of a web session (keyboard
    from the phone remote) via CDP Input.insertText."""
    s = STREAMS.get(req.match_info['sid'])
    if not s or not s.get('cdp_ws'):
        return web.json_response({'error': 'no session'}, status=404)
    try:
        text = (await req.json()).get('text', '')
    except Exception:
        text = ''
    _cdp_send(s['cdp_ws'], 'Input.insertText', {'text': text})
    return web.json_response({'ok': True, 'len': len(text)})


# Virtual cursor position per session for the phone trackpad.
MOUSE_POS = {}


async def handle_mouse(req):
    """Move/click a virtual mouse in a web session via CDP Input.dispatchMouseEvent."""
    sid = req.match_info['sid']
    s = STREAMS.get(sid)
    if not s or not s.get('cdp_ws'):
        return web.json_response({'error': 'no session'}, status=404)
    try:
        d = await req.json()
    except Exception:
        d = {}
    action = d.get('action', 'move')
    x, y = MOUSE_POS.get(sid, (640, 360))
    if action == 'move':
        x = max(0, min(1280, x + float(d.get('dx', 0)) * 1.5))
        y = max(0, min(720, y + float(d.get('dy', 0)) * 1.5))
        MOUSE_POS[sid] = (x, y)
        _cdp_send(s['cdp_ws'], 'Input.dispatchMouseEvent',
                  {'type': 'mouseMoved', 'x': x, 'y': y})
    else:
        btn = 'right' if action == 'right' else 'left'
        for t in ('mousePressed', 'mouseReleased'):
            _cdp_send(s['cdp_ws'], 'Input.dispatchMouseEvent',
                      {'type': t, 'x': x, 'y': y, 'button': btn,
                       'clickCount': 1})
    return web.json_response({'ok': True, 'x': x, 'y': y})


REMOTE_HTML = None


async def handle_remote(req):
    """Serve the phone/gamepad remote UI, branded per app via ?app=."""
    global REMOTE_HTML
    if REMOTE_HTML is None:
        try:
            REMOTE_HTML = (Path(__file__).parent / 'remote.html').read_text()
        except Exception:
            return web.json_response({'error': 'remote unavailable'}, status=500)
    app_id = req.query.get('app', 'game')
    title = {'crypticrealm': 'Cryptic Realm', 'romm': 'RomM'}.get(
        app_id, app_id.title())
    client = {'crypticrealm': 'roku-crypticrealm', 'romm': 'roku-romm'}.get(
        app_id, req.query.get('client', ''))
    html = (REMOTE_HTML.replace('__TITLE__', title)
            .replace('__CLIENT__', client))
    return web.Response(text=html, content_type='text/html')


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
    r.add_post('/api/stream/{sid}/text', handle_text)
    r.add_post('/api/stream/{sid}/mouse', handle_mouse)
    r.add_get('/api/stream/status', handle_status)
    r.add_get('/remote', handle_remote)
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
