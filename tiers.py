"""Platform → play-tier routing. Single source of truth for what is playable where.

Tier "local"  = EmulatorJS in the client browser (Xbox Edge runs the core itself).
Tier "stream" = server-side RetroArch captured and streamed (WebRTC or HLS).
None          = not playable (Windows/installer/mobile platforms).
"""

import re
from pathlib import Path

EJS_DATA_DIR = Path('/opt/romm-stream/emulatorjs/data')

# EmulatorJS platform slug → EJS *system* name (client-side).
#
# Must stay in step with EJS_CORES in RommForXbox/app.js, which is validated
# against the system list EmulatorJS actually ships
# (RommForXbox/tests/validate_cores.js). Four values here were not real systems —
# 'gbc', 'vice_x64', 'pcecd' and 'mame2003' — which made those platforms browse
# fine and fail only when the user pressed play.
EJS_CORES = {
    'nes': 'nes', 'famicom': 'nes', 'fds': 'nes',
    'snes': 'snes', 'sfam': 'snes', 'satellaview': 'snes',
    'n64': 'n64',
    'gb': 'gb', 'gbc': 'gb', 'gba': 'gba',
    'nds': 'nds', 'nintendo-dsi': 'nds',
    'genesis-slash-megadrive': 'segaMD', 'genesis': 'segaMD',
    'sega-pico': 'segaMD',
    'sms': 'segaMS', 'sg1000': 'segaMS', 'gamegear': 'segaGG',
    'sega32': 'sega32x', 'segacd': 'segaCD', 'saturn': 'segaSaturn',
    'psx': 'psx', 'ps': 'psx', 'psp': 'psp',
    'arcade': 'arcade', 'mame': 'mame',
    'neogeoaes': 'arcade', 'neogeomvs': 'arcade', 'neo-geo-pocket': 'ngp',
    'neo-geo-pocket-color': 'ngp',
    'atari2600': 'atari2600', 'atari-2600': 'atari2600',
    'atari5200': 'atari5200', 'atari7800': 'atari7800',
    'lynx': 'lynx', 'jaguar': 'jaguar',
    '3do': '3do', 'colecovision': 'coleco',
    'turbografx16--1': 'pce', 'turbografx-cd': 'pce',
    'turbografx-16-slash-pc-engine-cd': 'pce', 'supergrafx': 'pce',
    'pcfx': 'pcfx',
    'wonderswan': 'ws', 'wonderswan-color': 'ws',
    'virtualboy': 'vb', 'vic-20': 'vic20', 'c64': 'c64', 'c128': 'c128',
    'plus4': 'plus4',
    'amiga': 'amiga', 'amiga-cd32': 'amiga',
    'dos': 'dos',
    # ZX Spectrum and Amstrad CPC are deliberately absent: EmulatorJS has no
    # core for either, and mapping them anyway just moved the failure later.
}

# Server-side RetroArch cores, by core filename under CORES_DIR. A slug here is
# only offered to clients if the file is actually present, so listing one that has
# not been downloaded yet is safe.
RETROARCH_CORES = {
    # Installed and verified present.
    'ngc': 'dolphin_libretro.so',
    'wii': 'dolphin_libretro.so',
    'dc': 'flycast_libretro.so',
    'dreamcast': 'flycast_libretro.so',
    # flycast also runs Sega's arcade boards.
    'naomi': 'flycast_libretro.so',
    'atomiswave': 'flycast_libretro.so',
    'ps2': 'pcsx2_libretro.so',
    'saturn': 'mednafen_saturn_libretro.so',
    'n64': 'mupen64plus_next_libretro.so',
    'psp': 'ppsspp_libretro.so',
    'psx': 'mednafen_psx_hw_libretro.so',
    'arcade': 'fbneo_libretro.so',
    '3ds': 'citra_libretro.so',
    'new-nintendo-3ds': 'citra_libretro.so',
    # Light systems EmulatorJS has no core for. All digital-input, so they play
    # correctly even before the virtual pad lands.
    'msx': 'bluemsx_libretro.so',
    'msx2': 'bluemsx_libretro.so',
    'vectrex': 'vecx_libretro.so',
    'intellivision': 'freeintv_libretro.so',
    'sharp-x68000': 'px68k_libretro.so',
    # 2D software-rendered cores. These are the ONLY cores that render on a
    # GPU-less streaming host (Xvfb, no HW GLX): they rasterise in the CPU and
    # need no OpenGL context. EmulatorJS "handled" these before, but its WebGL
    # canvas paints black under Chromium's SwiftShader on the same host — so we
    # take them over natively with video_driver=sdl2. See is_software_core().
    'nes': 'fceumm_libretro.so', 'famicom': 'fceumm_libretro.so',
    'fds': 'fceumm_libretro.so',
    'snes': 'snes9x_libretro.so', 'sfam': 'snes9x_libretro.so',
    'satellaview': 'snes9x_libretro.so', 'sufami-turbo': 'snes9x_libretro.so',
    'gb': 'gambatte_libretro.so', 'gbc': 'gambatte_libretro.so',
    'gba': 'mgba_libretro.so',
    # Neo Geo CD. Its own core, not FBNeo: the AES/MVS drivers in fbneo are
    # cartridge hardware and will not load a cue or a chd. NeoCD declares
    # `cue|chd` (read from the core itself via retro_get_system_info) and has
    # no GL symbols at all -- 0, the same as genesis_plus_gx, against 1,258 in
    # flycast -- so it renders in software and runs on this GPU-less host.
    'neo-geo-cd': 'neocd_libretro.so',
    'neogeocd': 'neocd_libretro.so',
    'genesis-slash-megadrive': 'genesis_plus_gx_libretro.so',
    'genesis': 'genesis_plus_gx_libretro.so',
    'sega-mega-drive': 'genesis_plus_gx_libretro.so',
    'sms': 'genesis_plus_gx_libretro.so',
    'sega-master-system': 'genesis_plus_gx_libretro.so',
    'gamegear': 'genesis_plus_gx_libretro.so',
    'game-gear': 'genesis_plus_gx_libretro.so',
    'sg1000': 'genesis_plus_gx_libretro.so',
    'segacd': 'genesis_plus_gx_libretro.so',
    'sega-cd': 'genesis_plus_gx_libretro.so',
    'sega32': 'picodrive_libretro.so', 'sega32x': 'picodrive_libretro.so',
}

# Cores that rasterise on the CPU and run with video_driver=sdl2 — the only
# cores that can render on a GPU-less host (no HW GLX under Xvfb). Everything
# NOT listed here needs a real OpenGL/Vulkan context (Dolphin, PCSX2, Flycast,
# Mupen64Plus-Next, Citra, PPSSPP, mednafen-psx-HW) and renders BLACK here, so
# route() must refuse it on a GPU-less host rather than stream a black frame.
SOFTWARE_CORES = {
    'neocd_libretro.so',
    'fceumm_libretro.so', 'snes9x_libretro.so', 'gambatte_libretro.so',
    'mgba_libretro.so', 'genesis_plus_gx_libretro.so', 'picodrive_libretro.so',
    'bluemsx_libretro.so', 'vecx_libretro.so', 'freeintv_libretro.so',
    'px68k_libretro.so', 'fbneo_libretro.so',
}


def is_software_core(core_filename: str) -> bool:
    """True if this core renders in software (runs on a GPU-less host)."""
    return core_filename in SOFTWARE_CORES

# Never playable / never shown.
DENYLIST = {
    'win', 'windows', 'win3x', 'winxp', 'pc-98', 'android', 'ios',
    'amazon-fire-tv', 'amazon-alexa', 'airconsole', 'antstream',
    'steam', 'epic-games-store', 'gog', 'battlenet',
}


CORES_DIR = Path('/opt/romm-stream/cores')
SYSTEM_DIR = Path('/opt/romm-stream/system')

# Firmware each core needs before it can boot anything, as a list of
# requirements; each requirement is a list of acceptable filenames (relative to
# SYSTEM_DIR), any one of which satisfies it.
#
# This gate exists because a core with no firmware does not fail loudly — it
# launches, draws an error screen, and streams that error screen at a perfectly
# healthy 30 fps. Frame-rate checks cannot tell that apart from a running game,
# so "the stream connected" was being mistaken for "the game works".
#
# These files are console firmware and cannot be shipped; the operator has to
# supply them from their own hardware.
CORE_BIOS: dict[str, list[list[str]]] = {
    'flycast_libretro.so': [['dc_boot.bin'], ['dc_flash.bin']],
    'pcsx2_libretro.so': [['bios']],
    'mednafen_saturn_libretro.so': [['sega_101.bin', 'mpr-17933.bin']],
    'freeintv_libretro.so': [['exec.bin'], ['grom.bin']],
    'px68k_libretro.so': [['keropi/iplrom.dat'], ['keropi/cgrom.dat']],
    'bluemsx_libretro.so': [['Databases'], ['Machines']],
    'ppsspp_libretro.so': [['PPSSPP/ppge_atlas.zim', 'ppsspp/ppge_atlas.zim']],
    'mednafen_psx_hw_libretro.so': [['scph5500.bin', 'scph5501.bin',
                                     'scph5502.bin', 'scph1001.bin']],
    # Neo Geo CD needs one system BIOS and one LO ROM, and each has several
    # accepted names -- front loader, top loader, CDZ, and the UniBIOS. Listed
    # as two requirements with alternatives rather than twelve requirements,
    # because any one name in each group satisfies it. Taken from the core's
    # own info file (12 firmware entries under `neocd/`).
    'neocd_libretro.so': [
        ['neocd/neocd_f.rom', 'neocd/neocd_sf.rom', 'neocd/front-sp1.bin',
         'neocd/neocd_t.rom', 'neocd/neocd_st.rom', 'neocd/top-sp1.bin',
         'neocd/neocd_z.rom', 'neocd/neocd_sz.rom', 'neocd/neocd.bin',
         'neocd/uni-bioscd.rom'],
        ['neocd/ng-lo.rom', 'neocd/000-lo.lo'],
    ],
    # No firmware needed: Dolphin, Citra, Mupen64Plus-Next, Vecx. FBNeo wants
    # per-game BIOS zips that live with the ROMs, not here.
}


def bios_missing(core_filename: str,
                 system_dir: Path | None = None) -> list[str]:
    """Firmware requirements this core does not have satisfied."""
    root = system_dir or SYSTEM_DIR
    gaps = []
    for alternatives in CORE_BIOS.get(core_filename, []):
        if not any((root / a).exists() for a in alternatives):
            gaps.append(' or '.join(alternatives))
    return gaps


def core_installed(core_filename: str, cores_dir: Path | None = None) -> bool:
    """Whether a core binary is actually on disk.

    Routing checks this so the server never advertises a platform it cannot
    launch. Without it, adding a slug to RETROARCH_CORES before downloading the
    core produces the exact dead end this tier is meant to remove: the platform
    appears, the user presses play, and the runner raises FileNotFoundError.
    """
    if not core_filename:
        return False
    return ((cores_dir or CORES_DIR) / core_filename).is_file()


def _ejs_system_cores(data_dir: Path | None = None) -> dict[str, list[str]]:
    """EmulatorJS system name → the libretro cores that can serve it.

    Read from the shipped emulator.min.js `getCores()` table rather than
    hardcoded, because it is the only thing that knows the real relationship
    (system `psp` is served by a core file named `ppsspp`, not `psp`).
    """
    path = (data_dir or EJS_DATA_DIR) / 'emulator.min.js'
    try:
        src = path.read_text(errors='replace')
    except OSError:
        return {}
    i = src.find('getCores()')
    if i < 0:
        return {}
    j = src.find('{', src.find('=', i))
    depth = 0
    for k in range(j, len(src)):
        if src[k] == '{':
            depth += 1
        elif src[k] == '}':
            depth -= 1
            if depth == 0:
                break
    else:
        return {}
    return {m.group(1): re.findall(r'"([^"]+)"', m.group(2))
            for m in re.finditer(r'([A-Za-z0-9_]+):\[([^\]]*)\]', src[j:k + 1])}


def ejs_core_installed(system: str, data_dir: Path | None = None) -> bool:
    """Whether EmulatorJS can actually load this system on this server.

    The mirror of core_installed() for the local tier, and it was missing:
    route() trusted EJS_CORES membership alone, so a platform whose core had
    never been downloaded was advertised as playable and died at launch — the
    exact dead end core_installed() exists to prevent. Found with psp (1,182
    games) and dos, whose `ppsspp` and `dosbox_pure` cores are absent while 187
    others are present.

    A system absent from getCores() is treated as installed: that table is not
    exhaustive (3do is missing from it yet boots), and refusing to launch on its
    say-so would remove working platforms.
    """
    mapping = _ejs_system_cores(data_dir)
    options = mapping.get(system)
    if not options:
        return True
    cores_dir = (data_dir or EJS_DATA_DIR) / 'cores'
    try:
        have = {p.name.split('-legacy')[0].split('-wasm')[0]
                for p in cores_dir.glob('*.data')}
    except OSError:
        return True
    return any(o in have for o in options)


def route(platform_slug: str, cores_dir: Path | None = None,
          system_dir: Path | None = None,
          ejs_data_dir: Path | None = None) -> str | None:
    """Preferred tier for a platform slug, or None if unplayable."""
    slug = (platform_slug or '').lower()
    if slug in DENYLIST:
        return None
    if slug in EJS_CORES and ejs_core_installed(EJS_CORES[slug], ejs_data_dir):
        return 'local'
    # Falls through deliberately: a platform whose EmulatorJS core is missing may
    # still have a RetroArch core, which is how psp reaches ppsspp_libretro.
    core = RETROARCH_CORES.get(slug)
    if (core and core_installed(core, cores_dir)
            and not bios_missing(core, system_dir)):
        return 'stream'
    return None


def why_not(platform_slug: str, cores_dir: Path | None = None,
            system_dir: Path | None = None) -> str | None:
    """Why a platform is not streamable, in words, or None if it is.

    The client shows this instead of letting the user walk into a game that
    cannot boot.
    """
    slug = (platform_slug or '').lower()
    if slug in DENYLIST:
        return 'no emulator exists for this platform'
    if slug in EJS_CORES and ejs_core_installed(EJS_CORES[slug]):
        return None
    core = RETROARCH_CORES.get(slug)
    if not core:
        return 'no emulator exists for this platform'
    if not core_installed(core, cores_dir):
        return f'the {core} core is not installed on the stream server'
    gaps = bios_missing(core, system_dir)
    if gaps:
        return ('the stream server needs firmware for this platform: '
                + ', '.join(gaps))
    return None


def stream_core(platform_slug: str) -> str | None:
    """RetroArch core filename for a platform, or None.

    Returns the name whether or not it is installed; callers that need to know
    it will actually run should use core_installed() or route().
    """
    return RETROARCH_CORES.get((platform_slug or '').lower())


def streamable_slugs(cores_dir: Path | None = None,
                     system_dir: Path | None = None) -> list[str]:
    """Slugs the server can actually stream right now.

    The client asks for this rather than keeping its own copy of the core list,
    so a platform can never be offered on a server that cannot run it.

    Only software-rendered cores are reported: on a GPU-less streaming host the
    HW-GL cores load but exit with "Cannot open video driver" and would stream a
    black frame, so offering them to a client is worse than hiding them. A host
    WITH a GPU can widen this by dropping the is_software_core() gate.
    """
    return sorted(s for s, c in RETROARCH_CORES.items()
                  if is_software_core(c)
                  and core_installed(c, cores_dir)
                  and not bios_missing(c, system_dir))
