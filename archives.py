"""Make archived ROMs playable.

Almost the entire library ships as archives — 100% of wii and arcade, 99% of
ps2 and snes — and the heavy emulators cannot read them. Dolphin, PCSX2,
flycast and PPSSPP all want a real disc image on disk, so a `.7z` is simply an
unplayable file to them.

Two rules make this safe:

  * **A zip is not always an archive.** For MAME/FBNeo the `.zip` *is* the
    romset — the core opens it itself and expects its internal layout. Extracting
    one produces a directory of chip dumps that the core cannot load at all. So
    extraction is opt-in per platform (`NEEDS_EXTRACT`), never "expand anything
    compressed".
  * **Extraction is cached and capped.** A Wii image is ~4.7 GB, so extracting
    per launch would burn minutes and fill the disk. Extracted images live in an
    LRU cache that evicts by total size before each new extraction.
"""

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get('ROM_CACHE_DIR', '/opt/romm-stream/cache'))
# Root has ~72 GB free; stay well inside it so a full cache can never wedge the
# container. Roughly a dozen Wii images.
CACHE_MAX_BYTES = int(os.environ.get('ROM_CACHE_MAX_GB', '40')) * 1024**3

ARCHIVE_EXT = {'.7z', '.zip', '.rar'}

# Platforms whose emulator needs a plain image on disk. Deliberately excludes
# arcade/mame/fbneo and the EmulatorJS-only 2D systems, which handle zips
# natively.
NEEDS_EXTRACT = {
    'wii', 'ngc', 'wiiu', 'ps2', 'psp', 'dc', 'dreamcast', 'naomi',
    'atomiswave', 'saturn', 'segasaturn', '3ds', 'new-nintendo-3ds',
    'ps', 'psx', 'n64',
    # Neo Geo CD. The library ships these as .7z and .rar, and NeoCD declares
    # `cue|chd` -- it cannot open an archive at all. Without this the platform
    # routes to the stream tier, the launch is attempted, and the core is
    # handed a file it has no idea what to do with: the exact "route says yes,
    # game does not boot" gap the tier gate exists to close.
    'neo-geo-cd', 'neogeocd',
}

# What a bootable member looks like, per platform. First match wins, so the
# order encodes preference (a .rvz is better than a raw .iso when both exist).
MEMBER_EXT = {
    'wii':  ('.rvz', '.nkit.iso', '.wbfs', '.iso', '.gcm', '.ciso', '.gcz'),
    'ngc':  ('.rvz', '.nkit.iso', '.iso', '.gcm', '.ciso', '.gcz'),
    'wiiu': ('.wux', '.wud', '.iso'),
    'ps2':  ('.chd', '.iso', '.bin'),
    'psp':  ('.cso', '.iso'),
    'dc':   ('.chd', '.gdi', '.cdi'),
    'psx':  ('.chd', '.cue', '.bin', '.img'),
    'n64':  ('.z64', '.n64', '.v64'),
    '3ds':  ('.3ds', '.cci', '.cxi'),
}
MEMBER_EXT['neo-geo-cd'] = ('.chd', '.cue', '.bin', '.iso')
MEMBER_EXT['neogeocd'] = MEMBER_EXT['neo-geo-cd']
MEMBER_EXT['dreamcast'] = MEMBER_EXT['dc']
MEMBER_EXT['naomi'] = MEMBER_EXT['dc']
MEMBER_EXT['atomiswave'] = MEMBER_EXT['dc']
MEMBER_EXT['ps'] = MEMBER_EXT['psx']
MEMBER_EXT['new-nintendo-3ds'] = MEMBER_EXT['3ds']

# A .cue/.gdi names its data track by relative filename, so those companions
# must be extracted alongside the chosen member or the core loads nothing.
SIDECAR_FOR = {'.cue': ('.bin', '.img'), '.gdi': ('.bin', '.raw')}


def is_archive(path: Path) -> bool:
    return path.suffix.lower() in ARCHIVE_EXT


def needs_extraction(platform: str, path: Path) -> bool:
    return is_archive(path) and (platform or '').lower() in NEEDS_EXTRACT


def _list_members(archive: Path) -> list[str]:
    """Names inside the archive. bsdtar reads 7z, zip and rar uniformly."""
    try:
        out = subprocess.run(['bsdtar', '-tf', str(archive)],
                             capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning('cannot list %s: %s', archive.name, e)
        return []
    if out.returncode != 0:
        log.warning('cannot list %s: %s', archive.name, out.stderr[:200])
        return []
    return [ln for ln in out.stdout.splitlines() if ln.strip()]


def pick_member(platform: str, members: list[str]) -> str | None:
    """The bootable file inside the archive, by platform preference order."""
    wanted = MEMBER_EXT.get((platform or '').lower())
    files = [m for m in members if not m.endswith('/')]
    if not files:
        return None
    if wanted:
        for ext in wanted:
            hits = [m for m in files if m.lower().endswith(ext)]
            if hits:
                # Largest wins: multi-disc sets and stray tiny files coexist,
                # and the disc image is always the big one.
                return max(hits, key=len) if len(hits) == 1 else sorted(hits)[0]
    # No known extension: fall back to the single member if unambiguous.
    return files[0] if len(files) == 1 else None


def _cache_size() -> int:
    total = 0
    for p in CACHE_DIR.rglob('*'):
        try:
            if p.is_file():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _evict_for(need_bytes: int):
    """Drop least-recently-used entries until need_bytes fits under the cap."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    entries = [d for d in CACHE_DIR.iterdir() if d.is_dir()]
    entries.sort(key=lambda d: d.stat().st_atime)     # oldest access first
    while entries and _cache_size() + need_bytes > CACHE_MAX_BYTES:
        victim = entries.pop(0)
        log.info('evicting cached extraction %s', victim.name)
        shutil.rmtree(victim, ignore_errors=True)


def _extract(archive: Path, member: str, dest: Path) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    # bsdtar extracts a named member from 7z/zip/rar alike, and unlike `7z e`
    # it preserves nothing surprising about paths.
    try:
        r = subprocess.run(
            ['bsdtar', '-xf', str(archive), '-C', str(dest), member],
            capture_output=True, text=True, timeout=3600)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning('extract failed for %s: %s', archive.name, e)
        return False
    if r.returncode != 0:
        log.warning('extract failed for %s: %s', archive.name, r.stderr[:200])
        return False
    return True


def playable_path(platform: str, path: Path) -> Path | None:
    """A path the emulator can actually open.

    Returns `path` unchanged when it is already a plain image or a zip the core
    reads natively. Otherwise extracts the right member (cached) and returns it.
    None means the archive holds nothing bootable for this platform.
    """
    if not needs_extraction(platform, path):
        return path

    try:
        stat = path.stat()
    except OSError:
        return None
    # Keyed on identity, not just name, so a replaced archive re-extracts.
    key = f'{platform}-{path.stem}-{stat.st_size}-{int(stat.st_mtime)}'
    key = ''.join(c if c.isalnum() or c in '-_' else '_' for c in key)[:180]
    dest = CACHE_DIR / key

    if dest.is_dir():
        existing = [p for p in dest.rglob('*') if p.is_file()]
        if existing:
            os.utime(dest, None)                  # refresh LRU position
            wanted = MEMBER_EXT.get((platform or '').lower(), ())
            for ext in wanted:
                for p in existing:
                    if p.name.lower().endswith(ext):
                        return p
            return max(existing, key=lambda p: p.stat().st_size)
        shutil.rmtree(dest, ignore_errors=True)

    members = _list_members(path)
    if not members:
        return None
    member = pick_member(platform, members)
    if member is None:
        log.warning('no bootable member for %s in %s', platform, path.name)
        return None

    # Uncompressed size is unknown up front; the archive size is a floor, and
    # disc images compress ~2-4x, so reserve generously rather than overfill.
    _evict_for(stat.st_size * 4)

    wanted = [member]
    sidecars = SIDECAR_FOR.get(Path(member).suffix.lower())
    if sidecars:
        stem = Path(member).stem
        wanted += [m for m in members
                   if Path(m).stem == stem and m != member
                   and Path(m).suffix.lower() in sidecars]

    started = time.time()
    for m in wanted:
        if not _extract(path, m, dest):
            shutil.rmtree(dest, ignore_errors=True)
            return None
    out = dest / member
    if not out.is_file():
        found = [p for p in dest.rglob('*') if p.is_file()]
        if not found:
            shutil.rmtree(dest, ignore_errors=True)
            return None
        out = max(found, key=lambda p: p.stat().st_size)
    log.info('extracted %s -> %s in %.1fs', path.name, out.name,
             time.time() - started)
    return out
