"""Save-state persistence paths. Shared by RetroArch sessions and the
EmulatorJS upload/download endpoints so progress survives across tiers."""

from pathlib import Path

SAVES_DIR = Path('/opt/romm-stream/saves')
MAX_SAVE_BYTES = 16 * 1024 * 1024


def save_path(platform: str, rom_name: str, base: Path | None = None) -> Path:
    base = base if base is not None else SAVES_DIR
    """Resolve the .state path for a rom; reject path traversal."""
    for part in (platform, rom_name):
        if not part or '/' in part or '\\' in part or part.startswith('.') or '..' in part:
            raise ValueError('bad save path component: %r' % (part,))
    p = base / platform / (rom_name + '.state')
    return p


def session_dirs(platform: str, rom_name: str, base: Path | None = None) -> tuple[Path, Path]:
    """(savefile_dir, savestate_dir) for a RetroArch session, created."""
    root = save_path(platform, rom_name, base).parent / rom_name
    sf, st = root / 'sram', root / 'states'
    sf.mkdir(parents=True, exist_ok=True)
    st.mkdir(parents=True, exist_ok=True)
    return sf, st
