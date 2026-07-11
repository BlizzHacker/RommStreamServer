#!/usr/bin/env python3
"""RomM Streaming Server — EmulatorJS + FFmpeg → HLS for Roku."""

import asyncio, json, os, uuid, urllib.parse
from pathlib import Path
from aiohttp import web
import websocket

HLS_DIR = '/opt/romm-stream/hls'
ROM_BASE = '/mnt/usb1/roms'
STREAMS = {}

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

CORE_MAP = {
    'n64': 'n64', 'nes': 'nes', 'snes': 'snes',
    'genesis-slash-megadrive': 'segaMD', 'gba': 'gba', 'gb': 'gb', 'gbc': 'gbc',
    'nds': 'nds', 'psx': 'psx', 'psp': 'psp', 'arcade': 'arcade',
    'atari2600': 'atari2600', 'sega32': 'sega32x', 'segacd': 'segaCD',
    'saturn': 'saturn', '3do': '3do', 'jaguar': 'jaguar', 'lynx': 'lynx',
    'ngc': 'gamecube', 'wii': 'wii', 'dreamcast': 'dc'
}

KEY_MAP = {
    'up': 'ArrowUp', 'down': 'ArrowDown', 'left': 'ArrowLeft', 'right': 'ArrowRight',
    'a': 'KeyX', 'b': 'KeyZ', 'x': 'KeyS', 'y': 'KeyA',
    'l1': 'KeyQ', 'r1': 'KeyW', 'start': 'Enter', 'select': 'ShiftRight',
}

async def handle_start(req):
    try:
        data = await req.json()
    except Exception:
        data = {}
    rom_path = data.get('rom_path', '')
    platform = data.get('platform', 'n64')
    rom_name = data.get('name', 'game')

    sid = uuid.uuid4().hex[:8]
    stream_dir = Path(HLS_DIR) / sid
    stream_dir.mkdir(exist_ok=True)

    core = CORE_MAP.get(platform, platform)
    rom_url = ''
    rom_name_key = data.get('rom_name', '')
    if rom_name_key and platform:
        rom_path = ROM_BASE + '/' + platform + '/' + rom_name_key
        if os.path.exists(rom_path):
            rom_url = 'http://192.168.0.94:8091/roms/' + urllib.parse.quote(platform + '/' + rom_name_key)

    html = PLAYER_HTML.replace('__NAME__', rom_name).replace('__CORE__', core).replace('__ROM_URL__', rom_url)
    (stream_dir / 'player.html').write_text(html)

    display_num = 90 + len(STREAMS)
    display = ':' + str(display_num)
    xvfb = await asyncio.create_subprocess_exec(
        'Xvfb', display, '-screen', '0', '1280x720x24', '-ac',
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await asyncio.sleep(1)

    debug_port = 9222 + len(STREAMS)
    env = dict(os.environ)
    env['DISPLAY'] = display
    chrome = await asyncio.create_subprocess_exec(
        'chromium', '--display=' + display,
        '--no-sandbox', '--disable-gpu-sandbox',
        '--use-gl=angle', '--use-angle=swiftshader',
        '--enable-webgl', '--ignore-gpu-blocklist',
        '--window-size=1280,720', '--start-fullscreen',
        '--remote-debugging-port=' + str(debug_port),
        'http://192.168.0.94:8091/player/' + sid + '/player.html',
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=env)
    await asyncio.sleep(4)

    # Discover CDP page websocket URL
    cdp_ws = ''
    try:
        from urllib.request import urlopen
        cdp_resp = urlopen('http://localhost:' + str(debug_port) + '/json', timeout=5)
        pages = json.loads(cdp_resp.read())
        if pages:
            cdp_ws = pages[0].get('webSocketDebuggerUrl', '')
    except Exception:
        pass

    hls_path = str(stream_dir / 'stream.m3u8')
    seg_pattern = str(stream_dir / 'seg_%03d.ts')
    ffmpeg = await asyncio.create_subprocess_exec(
        'ffmpeg', '-f', 'x11grab', '-video_size', '1280x720',
        '-framerate', '30', '-i', display + '.0+0,0',
        '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency',
        '-b:v', '3M', '-maxrate', '3M', '-bufsize', '1.5M',
        '-g', '15', '-keyint_min', '15',
        '-hls_time', '1', '-hls_list_size', '5',
        '-hls_flags', 'delete_segments+program_date_time',
        '-hls_segment_filename', seg_pattern,
        '-f', 'hls', hls_path,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await asyncio.sleep(2)

    STREAMS[sid] = {
        'xvfb': xvfb, 'chrome': chrome, 'ffmpeg': ffmpeg,
        'display': display, 'debug_port': debug_port, 'rom_name': rom_name,
        'cdp_ws': cdp_ws
    }
    hls_url = 'http://192.168.0.94:8091/hls/' + sid + '/stream.m3u8'
    return web.json_response({'stream_id': sid, 'hls_url': hls_url, 'debug_port': debug_port})

async def handle_stop(req):
    sid = req.match_info['sid']
    s = STREAMS.pop(sid, None)
    if s:
        for p in [s['ffmpeg'], s['chrome'], s['xvfb']]:
            try:
                p.terminate()
                await asyncio.wait_for(p.wait(), timeout=3)
            except Exception:
                p.kill()
    return web.json_response({'ok': True})

async def handle_input(req):
    sid = req.match_info['sid']
    try:
        data = await req.json()
    except Exception:
        data = {}
    key = data.get('key', '')
    pressed = data.get('pressed', True)
    s = STREAMS.get(sid)
    if not s:
        return web.json_response({'error': 'stream not found'}, status=404)

    mapped = KEY_MAP.get(key, key)
    try:
        ws_url = s.get('cdp_ws', '')
        if ws_url:
            ws_url = ws_url.replace('localhost', '127.0.0.1')
            page_ws = websocket.create_connection(ws_url, timeout=2)
            method = 'Input.dispatchKeyEvent'
            params = {
                'type': 'keyDown' if pressed else 'keyUp',
                'key': mapped, 'code': mapped, 'windowsVirtualKeyCode': 0
            }
            page_ws.send(json.dumps({'id': 1, 'method': method, 'params': params}))
            page_ws.close()
    except Exception:
        pass
    return web.json_response({'ok': True, 'key': key, 'mapped': mapped})

async def handle_status(req):
    return web.json_response({'streams': [
        {'id': k, 'name': v['rom_name']} for k, v in STREAMS.items()
    ]})

async def main():
    app = web.Application()
    app.router.add_post('/api/stream/start', handle_start)
    app.router.add_post('/api/stream/{sid}/stop', handle_stop)
    app.router.add_post('/api/stream/{sid}/input', handle_input)
    app.router.add_get('/api/stream/status', handle_status)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8090)
    await site.start()
    print('Stream server on :8090')
    await asyncio.Future()

asyncio.run(main())
