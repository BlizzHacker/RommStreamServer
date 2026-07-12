import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from tiers import route, stream_core
from sessions import Allocator
from saves import save_path


def test_local_tier_platforms():
    for slug in ('snes', 'nes', 'gba', 'genesis-slash-megadrive', 'gbc', 'lynx'):
        assert route(slug) == 'local', slug


def test_stream_tier_platforms():
    for slug in ('ngc', 'wii', 'dc', 'ps2', '3ds'):
        assert route(slug) == 'stream', slug


def test_denylist_and_unknown():
    for slug in ('win', 'android', 'steam', 'totally-unknown-slug', '', None):
        assert route(slug) is None, slug


def test_local_preferred_when_both_exist():
    # psx/n64/psp exist in both maps; local (on-Xbox) wins.
    for slug in ('psx', 'n64', 'psp', 'arcade'):
        assert route(slug) == 'local'
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
