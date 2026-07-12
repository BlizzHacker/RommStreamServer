"""Platform → play-tier routing. Single source of truth for what is playable where.

Tier "local"  = EmulatorJS in the client browser (Xbox Edge runs the core itself).
Tier "stream" = server-side RetroArch captured and streamed (WebRTC or HLS).
None          = not playable (Windows/installer/mobile platforms).
"""

# EmulatorJS platform slug → EJS_core (client-side). Mirrors the Xbox client map.
EJS_CORES = {
    'nes': 'nes', 'famicom': 'nes', 'fds': 'nes',
    'snes': 'snes', 'sfam': 'snes',
    'n64': 'n64',
    'gb': 'gb', 'gbc': 'gbc', 'gba': 'gba',
    'nds': 'nds',
    'genesis-slash-megadrive': 'segaMD', 'sms': 'segaMS', 'gamegear': 'segaGG',
    'sega32': 'sega32x', 'segacd': 'segaCD', 'saturn': 'segaSaturn',
    'psx': 'psx', 'ps': 'psx', 'psp': 'psp',
    'arcade': 'arcade', 'mame': 'mame2003',
    'neogeoaes': 'arcade', 'neogeomvs': 'arcade', 'neo-geo-pocket': 'ngp',
    'neo-geo-pocket-color': 'ngp',
    'atari2600': 'atari2600', 'atari5200': 'atari5200', 'atari7800': 'atari7800',
    'lynx': 'lynx', 'jaguar': 'jaguar',
    '3do': '3do', 'colecovision': 'coleco',
    'turbografx16--1': 'pce', 'turbografx-16-slash-pc-engine-cd': 'pcecd',
    'wonderswan': 'ws', 'wonderswan-color': 'ws',
    'virtualboy': 'vb', 'vic-20': 'vic20', 'c64': 'vice_x64',
    'amiga': 'amiga', 'amstradcpc': 'amstradcpc', 'zxs': 'zx',
    'dos': 'dos',
}

# Heavy systems: server-side RetroArch core binaries (installed under CORES_DIR).
RETROARCH_CORES = {
    'ngc': 'dolphin_libretro.so',
    'wii': 'dolphin_libretro.so',
    'dc': 'flycast_libretro.so',
    'dreamcast': 'flycast_libretro.so',
    'ps2': 'pcsx2_libretro.so',
    'saturn': 'mednafen_saturn_libretro.so',
    'n64': 'mupen64plus_next_libretro.so',
    'psp': 'ppsspp_libretro.so',
    'psx': 'mednafen_psx_hw_libretro.so',
    'arcade': 'fbneo_libretro.so',
    '3ds': 'citra_libretro.so',
}

# Never playable / never shown.
DENYLIST = {
    'win', 'windows', 'win3x', 'winxp', 'pc-98', 'android', 'ios',
    'amazon-fire-tv', 'amazon-alexa', 'airconsole', 'antstream',
    'steam', 'epic-games-store', 'gog', 'battlenet',
}


def route(platform_slug: str) -> str | None:
    """Preferred tier for a platform slug, or None if unplayable."""
    slug = (platform_slug or '').lower()
    if slug in DENYLIST:
        return None
    if slug in EJS_CORES:
        return 'local'
    if slug in RETROARCH_CORES:
        return 'stream'
    return None


def stream_core(platform_slug: str) -> str | None:
    """RetroArch core filename for a platform, or None."""
    return RETROARCH_CORES.get((platform_slug or '').lower())
