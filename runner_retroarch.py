"""Headless RetroArch session runner: Xvfb + RetroArch + xdotool input.

Used by the WebRTC (Xbox) path and optionally by the HLS (Roku) path for
platforms EmulatorJS-in-headless-Chromium handles poorly.
"""

import asyncio
import os
from pathlib import Path

import saves
import tiers

CORES_DIR = Path('/opt/romm-stream/cores')
RA_CONFIG = Path('/opt/romm-stream/retroarch.cfg')

# Virtual-gamepad button → X11 keysym matching RetroArch's default
# "RetroPad on keyboard" binds (user 1).
RA_KEYS = {
    'up': 'Up', 'down': 'Down', 'left': 'Left', 'right': 'Right',
    'a': 'x', 'b': 'z', 'x': 's', 'y': 'a',
    'l1': 'q', 'r1': 'w', 'l2': 'e', 'r2': 'r',
    'l3': 't', 'r3': 'y',
    'start': 'Return', 'select': 'shift',
    'menu': 'F1',  # RetroArch quick menu
}


def key_for(button: str) -> str | None:
    return RA_KEYS.get((button or '').lower())


def core_path(platform_slug: str, cores_dir: Path = CORES_DIR) -> Path | None:
    core = tiers.stream_core(platform_slug)
    return (cores_dir / core) if core else None


async def start_xvfb(display_num: int):
    proc = await asyncio.create_subprocess_exec(
        'Xvfb', f':{display_num}', '-screen', '0', '1280x720x24', '-ac',
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await asyncio.sleep(1)
    return proc


async def start_retroarch(platform_slug: str, rom_path: str, display_num: int,
                          rom_name: str):
    core = core_path(platform_slug)
    if core is None or not core.exists():
        raise FileNotFoundError(f'no RetroArch core for {platform_slug}')
    sram, states = saves.session_dirs(platform_slug, rom_name)
    env = dict(os.environ, DISPLAY=f':{display_num}')
    args = ['retroarch', '-L', str(core), rom_path, '--fullscreen',
            '--appendconfig', str(RA_CONFIG)] if RA_CONFIG.exists() else \
           ['retroarch', '-L', str(core), rom_path, '--fullscreen']
    env['XDG_RUNTIME_DIR'] = '/tmp'
    proc = await asyncio.create_subprocess_exec(
        *args,
        env=dict(env, RETROARCH_SAVEFILE_DIR=str(sram),
                 RETROARCH_SAVESTATE_DIR=str(states)),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await asyncio.sleep(3)
    return proc


async def send_key(display_num: int, button: str, pressed: bool) -> bool:
    """Inject a key event into the session's X display via xdotool."""
    keysym = key_for(button)
    if not keysym:
        return False
    action = 'keydown' if pressed else 'keyup'
    proc = await asyncio.create_subprocess_exec(
        'xdotool', action, '--clearmodifiers', keysym,
        env=dict(os.environ, DISPLAY=f':{display_num}'),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await proc.wait()
    return True


async def terminate(*procs):
    for p in procs:
        if p is None:
            continue
        try:
            p.terminate()
            await asyncio.wait_for(p.wait(), timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
