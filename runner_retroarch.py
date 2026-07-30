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
LOG_DIR = Path('/opt/romm-stream/logs')

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
    # -v goes on the command line, not in the config: log_verbosity in an
    # appended config did not take effect, and without a log there is no way to
    # tell "the pad was never bound" from "the core cannot find its firmware" —
    # both look like a session that streams a still image.
    args = ['retroarch', '-v', '-L', str(core), rom_path, '--fullscreen']
    if RA_CONFIG.exists():
        args += ['--appendconfig', str(RA_CONFIG)]
    env['XDG_RUNTIME_DIR'] = '/tmp'
    # HOME is deliberately NOT set. The unit provides none, which means RetroArch
    # never finds ~/.config/retroarch/retroarch.cfg and runs on defaults — and
    # that is the only configuration in which it runs at all here. Setting
    # HOME=/root makes it abort with SIGABRT during startup *even with no config
    # file present*, so the missing HOME is load-bearing rather than an oversight.
    #
    # The cost is that config-driven settings (notably input_joypad_driver, needed
    # to bind a virtual pad) cannot be applied yet. See docs/analog-input.md.
    # Keep a log per display. Discarding RetroArch's output made "the pad was
    # never bound" and "the core could not find its firmware" both look identical
    # from outside — a silent session that streams a still image.
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f'ra-{display_num}.log'
    try:
        log_file = open(log_path, 'wb')
    except OSError:
        log_file = None
    proc = await asyncio.create_subprocess_exec(
        *args,
        env=dict(env, RETROARCH_SAVEFILE_DIR=str(sram),
                 RETROARCH_SAVESTATE_DIR=str(states)),
        stdout=log_file or asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.STDOUT if log_file
        else asyncio.subprocess.DEVNULL)
    if log_file:
        log_file.close()          # the child holds its own copy of the fd
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
