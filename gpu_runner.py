#!/usr/bin/env python3
"""GPU stream runner — the heavy-3D half of the RomM stream fleet.

Runs on the mw-laptop VM (192.168.0.201) that has the RTX 3050 passed through
(shared with ArcForge's ComfyUI, which we never touch). It renders the heavy 3D
platforms CT104 cannot — N64, PS1/PS2, GameCube/Wii, Dreamcast, Saturn, PSP —
with hardware OpenGL (glcore), then serves them as HLS exactly like CT104.

Deliberately small and self-contained (not a fork of the full server) because
this box is disk-tight and only needs: start a session, stream it, take input,
stop. The 2D software cores stay on CT104; a front router picks which host a
platform goes to.

Endpoints (same shape as the main server so clients don't care which host):
  POST /api/stream/start   {platform, rom_name, name, client}
  POST /api/stream/{sid}/input  {key, pressed}
  POST /api/stream/{sid}/stop
  GET  /api/stream/status
  GET  /api/play/streamable
  GET  /hls/{sid}/...  (served by this process)
"""
import asyncio
import json
import os
import shutil
import urllib.parse
import uuid
from pathlib import Path

from aiohttp import web
import aiohttp

BASE = Path('/opt/romm-gpu')
CORES = BASE / 'cores'
HLS = BASE / 'hls'
ROMCACHE = BASE / 'romcache'
LOGS = BASE / 'logs'
for d in (HLS, ROMCACHE, LOGS):
    d.mkdir(parents=True, exist_ok=True)

# This host's public base (its own IP) for HLS URLs the client fetches.
PUBLIC = os.environ.get('GPU_PUBLIC', 'http://192.168.0.201:8090')
# Where to pull ROM bytes from (CT104 nginx serves /roms/<platform>/<file>).
ROM_SRC = os.environ.get('ROM_SRC', 'http://192.168.0.94:8091/roms')
DISPLAY_BASE = 80          # Xvfb display numbers 80..87
PORT_BASE = 9280           # unused here but reserved

# Heavy platforms this GPU host renders, slug -> core filename.
GPU_CORES = {
    'n64': 'mupen64plus_next_libretro.so',
    'psx': 'mednafen_psx_hw_libretro.so', 'ps': 'mednafen_psx_hw_libretro.so',
    'ps2': 'pcsx2_libretro.so',
    'ngc': 'dolphin_libretro.so', 'wii': 'dolphin_libretro.so',
    'dc': 'flycast_libretro.so', 'dreamcast': 'flycast_libretro.so',
    'naomi': 'flycast_libretro.so', 'atomiswave': 'flycast_libretro.so',
    'saturn': 'mednafen_saturn_libretro.so',
    'psp': 'ppsspp_libretro.so',
}
# RetroPad button -> X11 keysym, matching RetroArch default keyboard binds.
KEYS = {'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right',
        'a': 'x', 'b': 'z', 'x': 's', 'y': 'a', 'l1': 'q', 'r1': 'w',
        'l2': 'e', 'r2': 'r', 'l3': 't', 'r3': 'y',
        'start': 'Return', 'select': 'shift', 'menu': 'F1'}

STREAMS = {}
_free_displays = list(range(DISPLAY_BASE, DISPLAY_BASE + 8))


async def _run(*args, **kw):
    return await asyncio.create_subprocess_exec(*args, **kw)


async def fetch_rom(platform, rom_name):
    """Pull the ROM to a local cache (once) and return its path."""
    dest = ROMCACHE / platform / rom_name
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = f'{ROM_SRC}/{urllib.parse.quote(platform)}/{urllib.parse.quote(rom_name)}'
    async with aiohttp.ClientSession() as s:
        async with s.get(url, timeout=aiohttp.ClientTimeout(total=120)) as r:
            if r.status != 200:
                return None
            with open(dest, 'wb') as f:
                async for chunk in r.content.iter_chunked(1 << 16):
                    f.write(chunk)
    return dest if dest.stat().st_size > 0 else None


async def start_ffmpeg(display, stream_dir):
    hls = str(stream_dir / 'stream.m3u8')
    seg = str(stream_dir / 'seg_%03d.ts')
    return await _run(
        'ffmpeg', '-f', 'x11grab', '-video_size', '1280x720',
        '-framerate', '30', '-i', f'{display}.0+0,0',
        '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
        '-c:v', 'libx264', '-preset', 'veryfast', '-tune', 'zerolatency',
        '-profile:v', 'high', '-level', '4.0', '-pix_fmt', 'yuv420p',
        '-b:v', '4M', '-maxrate', '4M', '-bufsize', '2M',
        '-g', '30', '-keyint_min', '30', '-sc_threshold', '0',
        '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
        '-hls_time', '1', '-hls_list_size', '3',
        '-hls_flags', 'delete_segments+independent_segments',
        '-master_pl_name', 'master.m3u8',
        '-hls_segment_filename', seg, '-f', 'hls', hls,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)


async def handle_start(req):
    try:
        data = await req.json()
    except Exception:
        data = {}
    platform = (data.get('platform') or '').lower()
    rom_name = data.get('rom_name', '')
    name = data.get('name', rom_name)
    client = data.get('client', '')
    core = GPU_CORES.get(platform)
    if not core or not (CORES / core).exists():
        return web.json_response({'error': f'no GPU core for {platform}'}, status=404)

    # Reap prior sessions from the same client.
    for old in [k for k, v in STREAMS.items() if v.get('client') == client]:
        await _stop(old)

    rom = await fetch_rom(platform, rom_name)
    if rom is None:
        return web.json_response({'error': 'rom not found'}, status=404)
    if not _free_displays:
        return web.json_response({'error': 'server busy'}, status=503)
    dn = _free_displays.pop(0)
    display = f':{dn}'
    sid = uuid.uuid4().hex[:8]
    stream_dir = HLS / sid
    stream_dir.mkdir(parents=True, exist_ok=True)

    xvfb = await _run('Xvfb', display, '-screen', '0', '1280x720x24',
                      '+extension', 'GLX', '+render', '-ac',
                      stdout=asyncio.subprocess.DEVNULL,
                      stderr=asyncio.subprocess.DEVNULL)
    await asyncio.sleep(2)
    # glcore = hardware OpenGL on the RTX 3050 (proven to render DBKart in 3D).
    cfg = LOGS / f'gpu-{dn}.cfg'
    cfg.write_text('video_driver = "glcore"\naudio_driver = "null"\n')
    log = open(LOGS / f'ra-{dn}.log', 'wb')
    ra = await _run('retroarch', '-v', '-L', str(CORES / core), str(rom),
                    '--fullscreen', '--appendconfig', str(cfg),
                    env=dict(os.environ, DISPLAY=display, XDG_RUNTIME_DIR='/tmp'),
                    stdout=log, stderr=asyncio.subprocess.STDOUT)
    log.close()
    await asyncio.sleep(3)
    ffmpeg = await start_ffmpeg(display, stream_dir)

    master = stream_dir / 'master.m3u8'
    for _ in range(80):
        if master.exists() and len(list(stream_dir.glob('seg_*.ts'))) >= 2:
            break
        await asyncio.sleep(0.2)

    STREAMS[sid] = {'xvfb': xvfb, 'retroarch': ra, 'ffmpeg': ffmpeg,
                    'display_num': dn, 'rom_name': name, 'client': client,
                    'platform': platform, 'engine': 'retroarch-gpu'}
    return web.json_response({
        'stream_id': sid, 'engine': 'retroarch-gpu',
        'hls_url': f'{PUBLIC}/hls/{sid}/master.m3u8'})


async def handle_input(req):
    sid = req.match_info['sid']
    s = STREAMS.get(sid)
    if not s:
        return web.json_response({'error': 'stream not found'}, status=404)
    try:
        d = await req.json()
    except Exception:
        d = {}
    keysym = KEYS.get((d.get('key') or '').lower())
    if not keysym:
        return web.json_response({'ok': False})
    action = 'keydown' if d.get('pressed', True) else 'keyup'
    p = await _run('xdotool', action, '--clearmodifiers', keysym,
                   env=dict(os.environ, DISPLAY=f":{s['display_num']}"),
                   stdout=asyncio.subprocess.DEVNULL,
                   stderr=asyncio.subprocess.DEVNULL)
    await p.wait()
    return web.json_response({'ok': True})


async def _stop(sid):
    s = STREAMS.pop(sid, None)
    if not s:
        return
    for p in (s.get('ffmpeg'), s.get('retroarch'), s.get('xvfb')):
        try:
            if p:
                p.terminate()
        except Exception:
            pass
    _free_displays.append(s['display_num'])
    _free_displays.sort()
    shutil.rmtree(HLS / sid, ignore_errors=True)


async def handle_stop(req):
    await _stop(req.match_info['sid'])
    return web.json_response({'ok': True})


async def handle_status(req):
    return web.json_response({'streams': [
        {'id': k, 'name': v['rom_name'], 'engine': v['engine'],
         'client': v.get('client', ''), 'platform': v.get('platform', '')}
        for k, v in STREAMS.items()]})


async def handle_streamable(req):
    slugs = sorted(s for s, c in GPU_CORES.items() if (CORES / c).exists())
    return web.json_response({'streamable': slugs, 'unavailable': {}})


def make_app():
    app = web.Application()
    r = app.router
    r.add_post('/api/stream/start', handle_start)
    r.add_post('/api/stream/{sid}/input', handle_input)
    r.add_post('/api/stream/{sid}/stop', handle_stop)
    r.add_get('/api/stream/status', handle_status)
    r.add_get('/api/play/streamable', handle_streamable)
    r.add_static('/hls/', str(HLS))
    return app


if __name__ == '__main__':
    web.run_app(make_app(), host='0.0.0.0', port=8090)
