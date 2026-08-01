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
    # +extension GLX +render: RetroArch's "gl" video driver needs a GLX visual
    # to bind llvmpipe (software OpenGL). Without these Xvfb advertises no GLX
    # and the gl driver cannot create a context — the software 2D cores then
    # render black. These flags cost nothing for the Chromium/web path.
    proc = await asyncio.create_subprocess_exec(
        'Xvfb', f':{display_num}', '-screen', '0', '1280x720x24',
        '+extension', 'GLX', '+render', '-ac',
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await asyncio.sleep(1)
    return proc


async def start_retroarch(platform_slug: str, rom_path: str, display_num: int,
                          rom_name: str, pad_event: str | None = None):
    core = core_path(platform_slug)
    if core is None or not core.exists():
        raise FileNotFoundError(f'no RetroArch core for {platform_slug}')
    sram, states = saves.session_dirs(platform_slug, rom_name)
    env = dict(os.environ, DISPLAY=f':{display_num}')
    # -v goes on the command line, not in the config: log_verbosity in an
    # appended config did not take effect, and without a log there is no way to
    # tell "the pad was never bound" from "the core cannot find its firmware" —
    # both look like a session that streams a still image.
    #
    # dbus-run-session: the historical SIGABRT-on-config was never RetroArch's
    # config parser. This Debian build links GameMode; applying a config makes
    # its client library reach for a D-Bus session bus, and when autolaunch
    # fails (no $DISPLAY on the bus side, container) it calls
    # dbus_connection_unref(NULL) — an assertion that aborts the process.
    # Giving it a real (empty) session bus makes that path succeed cleanly.
    #
    # -c, not --appendconfig: with no base config to append to (no HOME), 1.14
    # silently ignores --appendconfig — the earlier binds file was never read
    # and every session ran on pure defaults.
    args = ['dbus-run-session', '--',
            'retroarch', '-v', '-L', str(core), rom_path, '--fullscreen']
    if RA_CONFIG.exists():
        args += ['-c', str(RA_CONFIG)]
    # Software 2D cores render through RetroArch's default "gl" driver, which on
    # this GPU-less host runs on Mesa's llvmpipe (software OpenGL) — snes9x /
    # fceumm / gambatte / genesis-plus-gx are light enough that llvmpipe draws
    # them at full speed (verified: Chrono Trigger's title pendulum). Two things
    # matter and both are set below via a read-only appendconfig:
    #   * --set-shader "" (the CLI flag, added to args): a stale GLSL shader
    #     preset otherwise loads and the frame comes out black.
    #   * audio_driver = null: there is no audio device, and the default alsa
    #     driver's failure is loud but harmless; null keeps the log clean.
    # Do NOT force video_driver = "sdl2" here: on this box sdl2 initialises, then
    # falls back to the "null" display driver and paints nothing (black). "gl"
    # on llvmpipe is the path that actually renders. Heavy HW-render cores
    # (mupen64plus/dolphin/pcsx2/…) still can't run — llvmpipe rejects their FBO
    # setup — which is why is_software_core() gates what we launch at all.
    if tiers.is_software_core(core.name):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        soft_cfg = LOG_DIR / f'soft-{display_num}.cfg'
        soft_cfg.write_text('audio_driver = "null"\n'
                            'video_shader_enable = "false"\n')
        args += ['--set-shader', '', '--appendconfig', str(soft_cfg)]
    env['XDG_RUNTIME_DIR'] = '/tmp'
    if pad_event:
        # SDL2's udev-less fallback opens exactly this node; see vpad.event_node.
        env['SDL_JOYSTICK_DEVICE'] = pad_event
    # HOME is still deliberately NOT set: RetroArch must never discover a stray
    # ~/.config/retroarch config that fights the -c file above.
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
