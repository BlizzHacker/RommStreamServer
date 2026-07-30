import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
import tiers
from tiers import route, stream_core, streamable_slugs
from sessions import Allocator
from saves import save_path


@pytest.fixture
def firmware(tmp_path):
    """A system directory satisfying every core's firmware requirement.

    Routing is firmware-aware, so tests about core availability have to supply
    this or they are really testing the BIOS gate by accident.
    """
    root = tmp_path / 'firmware'
    root.mkdir()
    for reqs in tiers.CORE_BIOS.values():
        for alternatives in reqs:
            target = root / alternatives[0]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'')
    return root


@pytest.fixture
def cores(tmp_path):
    """A cores directory with every known core present.

    Routing is existence-aware, so without this the stream-tier assertions would
    pass or fail depending on what happens to be installed on the machine running
    the tests.
    """
    for name in set(tiers.RETROARCH_CORES.values()):
        (tmp_path / name).write_bytes(b'')
    return tmp_path


def test_local_tier_platforms():
    for slug in ('snes', 'nes', 'gba', 'genesis-slash-megadrive', 'genesis',
                 'gbc', 'lynx', 'c64', 'turbografx-cd', 'supergrafx'):
        assert route(slug) == 'local', slug


def test_stream_tier_platforms(cores, firmware):
    for slug in ('ngc', 'wii', 'dc', 'ps2', '3ds', 'naomi', 'atomiswave',
                 'msx', 'vectrex', 'intellivision', 'sharp-x68000'):
        assert route(slug, cores_dir=cores, system_dir=firmware) == 'stream', slug


def test_denylist_and_unknown():
    for slug in ('win', 'android', 'steam', 'totally-unknown-slug', '', None):
        assert route(slug) is None, slug


def test_stream_platform_not_offered_without_its_core(tmp_path):
    """The whole point of existence-aware routing.

    Advertising a platform whose core is not downloaded gives the user a grid they
    can browse and a game that cannot launch — the failure mode this tier exists
    to remove.
    """
    assert route('ngc', cores_dir=tmp_path) is None
    assert route('msx', cores_dir=tmp_path) is None
    assert streamable_slugs(tmp_path) == []


def test_streamable_slugs_reflects_what_is_installed(tmp_path, firmware):
    (tmp_path / 'flycast_libretro.so').write_bytes(b'')
    got = streamable_slugs(tmp_path, firmware)
    assert 'dc' in got and 'naomi' in got and 'atomiswave' in got
    assert 'ngc' not in got            # dolphin absent


def test_every_ejs_core_is_a_real_emulatorjs_system():
    """Mirrors RommForXbox/tests/validate_cores.js.

    'gbc', 'vice_x64', 'pcecd' and 'mame2003' all lived here and none of them are
    real EmulatorJS systems, so those platforms browsed fine and failed on play.
    """
    systems = {
        '3do', 'amiga', 'arcade', 'atari2600', 'atari5200', 'atari7800', 'c128',
        'c64', 'coleco', 'dos', 'gb', 'gba', 'jaguar', 'lynx', 'mame', 'n64',
        'nds', 'nes', 'ngp', 'pce', 'pcfx', 'pet', 'plus4', 'psp', 'psx',
        'sega', 'sega32x', 'segaCD', 'segaGG', 'segaMD', 'segaMS',
        'segaSaturn', 'snes', 'vb', 'vic20', 'ws',
    }
    bad = {s: c for s, c in tiers.EJS_CORES.items() if c not in systems}
    assert not bad, f'not real EmulatorJS systems: {bad}'


def test_local_preferred_when_both_exist(cores):
    # psx/n64/psp exist in both maps; local (on-Xbox) wins.
    for slug in ('psx', 'n64', 'psp', 'arcade'):
        assert route(slug, cores_dir=cores) == 'local'
        assert stream_core(slug) is not None  # still streamable for Roku


def test_allocator_no_collision():
    a = Allocator(max_sessions=2)
    d1, p1 = a.acquire()
    d2, p2 = a.acquire()
    assert d1 != d2 and p1 != p2
    with pytest.raises(RuntimeError):
        a.acquire()
    a.release(d1)
    d3, _ = a.acquire()
    assert d3 == d1  # slot reusable only after release
    assert d3 != d2


def test_save_path_traversal_rejected():
    for bad in ('..', 'a/b', 'a\\b', '.hidden', ''):
        with pytest.raises(ValueError):
            save_path(bad, 'game')
        with pytest.raises(ValueError):
            save_path('snes', bad)


def test_save_path_shape(tmp_path):
    p = save_path('snes', 'Chrono Trigger (USA).sfc', base=tmp_path)
    assert p == tmp_path / 'snes' / 'Chrono Trigger (USA).sfc.state'


def test_bios_gate_blocks_a_core_that_cannot_boot(cores, tmp_path):
    """A core with no firmware launches and draws an error screen.

    It then streams that error screen at a healthy 30 fps, so frame-rate checks
    cannot tell it from a working game — which is exactly how "the stream
    connected" got mistaken for "the game works". The gate has to be presence of
    firmware, not liveness of the stream.
    """
    empty_system = tmp_path / 'system'
    empty_system.mkdir()
    # Dreamcast needs dc_boot.bin + dc_flash.bin.
    assert tiers.route('dc', cores_dir=cores, system_dir=empty_system) is None
    why = tiers.why_not('dc', cores_dir=cores, system_dir=empty_system)
    assert 'firmware' in why and 'dc_boot.bin' in why

    # Dolphin needs none, so GameCube stays available.
    assert tiers.route('ngc', cores_dir=cores, system_dir=empty_system) == 'stream'
    # Vectrex needs none either — the one system proven to actually run.
    assert tiers.route('vectrex', cores_dir=cores, system_dir=empty_system) == 'stream'


def test_bios_gate_opens_once_firmware_is_supplied(cores, tmp_path):
    system = tmp_path / 'system'
    system.mkdir()
    (system / 'dc_boot.bin').write_bytes(b'')
    assert tiers.route('dc', cores_dir=cores, system_dir=system) is None  # still need flash
    (system / 'dc_flash.bin').write_bytes(b'')
    assert tiers.route('dc', cores_dir=cores, system_dir=system) == 'stream'
    assert tiers.why_not('dc', cores_dir=cores, system_dir=system) is None


def test_saturn_accepts_either_bios_name(cores, tmp_path):
    system = tmp_path / 'system'
    system.mkdir()
    (system / 'mpr-17933.bin').write_bytes(b'')
    assert tiers.route('saturn', cores_dir=cores, system_dir=system) == 'local'
    assert not tiers.bios_missing('mednafen_saturn_libretro.so', system)


def test_streamable_excludes_platforms_missing_firmware(cores, tmp_path):
    system = tmp_path / 'system'
    system.mkdir()
    got = tiers.streamable_slugs(cores, system)
    assert 'vectrex' in got and 'ngc' in got and 'wii' in got
    for needs_bios in ('dc', 'ps2', 'intellivision', 'sharp-x68000', 'msx'):
        assert needs_bios not in got, needs_bios
