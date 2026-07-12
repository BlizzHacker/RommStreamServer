import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from aiohttp.test_utils import TestClient, TestServer

import saves
import server


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(saves, 'SAVES_DIR', tmp_path / 'saves')
    app = server.build_app()
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_route_local(client):
    r = await client.get('/api/play/route?platform=snes')
    assert r.status == 200 and (await r.json())['tier'] == 'local'


async def test_route_stream(client):
    r = await client.get('/api/play/route?platform=wii')
    assert (await r.json())['tier'] == 'stream'


async def test_route_denied(client):
    r = await client.get('/api/play/route?platform=win')
    assert r.status == 404


async def test_save_roundtrip(client):
    body = b'\x00SAVESTATE' * 100
    r = await client.put('/api/saves/snes/Chrono.sfc', data=body)
    assert r.status == 200
    r = await client.get('/api/saves/snes/Chrono.sfc')
    assert r.status == 200 and await r.read() == body


async def test_save_missing_and_bad(client):
    assert (await client.get('/api/saves/snes/none.sfc')).status == 404
    assert (await client.put('/api/saves/snes/..%2Fevil', data=b'x')).status in (400, 404)


async def test_romfile_traversal_blocked(client):
    r = await client.get('/api/romfile/snes/..%2F..%2Fetc%2Fpasswd')
    assert r.status == 404
