"""aiortc WebRTC out: capture the session's Xvfb display + pulse audio,
answer browser offers, receive gamepad input on a data channel.

aiortc/av are imported lazily so unit tests run without them installed.
"""

import asyncio
import json
import logging

import runner_retroarch

log = logging.getLogger('webrtc')

ICE_SERVERS = [{'urls': 'stun:stun.l.google.com:19302'}]


def _media_players(display_num: int):
    from aiortc.contrib.media import MediaPlayer
    video = MediaPlayer(
        f':{display_num}.0+0,0', format='x11grab',
        options={'framerate': '30', 'video_size': '1280x720',
                 'draw_mouse': '0'})
    audio = None
    try:
        audio = MediaPlayer(f'romm{display_num}.monitor', format='pulse')
    except Exception as e:
        log.warning('no audio capture: %s', e)
    return video, audio


def _attach_input(pc, display_num: int):
    """Wire the 'input' data channel to the session's virtual input."""
    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            try:
                m = json.loads(message)
            except Exception:
                return
            if 'axes' in m:
                # Analog sticks need a uinput virtual pad; xdotool can only send
                # key events. Accepted and dropped for now so the protocol does
                # not have to change when that lands — the client also synthesises
                # d-pad presses from the stick, so movement still works.
                return
            asyncio.ensure_future(runner_retroarch.send_key(
                display_num, m.get('key', ''), bool(m.get('pressed'))))


async def answer_offer(display_num: int, offer_sdp: str, on_close):
    """Answer a complete SDP offer over HTTP and return (pc, answer_sdp).

    The HTTP path exists because the Xbox shell cannot use the WebSocket one: the
    packaged app is served from an https origin, so a ws:// URL to a LAN stream
    server is blocked mixed content, and WebSockets cannot be routed through the
    host's request interception the way HTTP can. Media is peer-to-peer UDP and
    unaffected either way.
    """
    from aiortc import (RTCPeerConnection, RTCSessionDescription,
                        RTCConfiguration, RTCIceServer)

    pc = RTCPeerConnection(RTCConfiguration(
        iceServers=[RTCIceServer(**s) for s in ICE_SERVERS]))
    video, audio = _media_players(display_num)
    pc.addTrack(video.video)
    if audio and audio.audio:
        pc.addTrack(audio.audio)

    _attach_input(pc, display_num)
    closed = asyncio.Event()

    async def close():
        if closed.is_set():
            return
        closed.set()
        try:
            await pc.close()
        finally:
            for mp in (video, audio):
                if mp:
                    try:
                        mp._stop(mp.video or mp.audio)  # noqa: best-effort
                    except Exception:
                        pass
            await on_close()

    @pc.on('connectionstatechange')
    async def on_state():
        if pc.connectionState in ('failed', 'closed', 'disconnected'):
            await close()

    await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp,
                                                        type='offer'))
    answer = await pc.createAnswer()
    # aiortc finishes ICE gathering before this resolves, so the SDP it produces
    # is complete — which is what lets signaling be a single request/response.
    await pc.setLocalDescription(answer)
    return pc, pc.localDescription.sdp, close


async def run_peer(ws, display_num: int, on_close):
    """Drive one WebRTC peer over an aiohttp WebSocketResponse used as the
    signaling channel. Messages: {type: offer|ice}, replies {type: answer|ice}.
    Data channel 'input' carries {"key": str, "pressed": bool}."""
    from aiortc import RTCPeerConnection, RTCSessionDescription, RTCConfiguration, RTCIceServer

    pc = RTCPeerConnection(RTCConfiguration(
        iceServers=[RTCIceServer(**s) for s in ICE_SERVERS]))
    video, audio = _media_players(display_num)
    pc.addTrack(video.video)
    if audio and audio.audio:
        pc.addTrack(audio.audio)

    @pc.on('datachannel')
    def on_datachannel(channel):
        @channel.on('message')
        def on_message(message):
            try:
                m = json.loads(message)
            except Exception:
                return
            asyncio.ensure_future(runner_retroarch.send_key(
                display_num, m.get('key', ''), bool(m.get('pressed'))))

    @pc.on('connectionstatechange')
    async def on_state():
        if pc.connectionState in ('failed', 'closed', 'disconnected'):
            await close()

    closed = asyncio.Event()

    async def close():
        if closed.is_set():
            return
        closed.set()
        try:
            await pc.close()
        finally:
            for mp in (video, audio):
                if mp:
                    try:
                        mp._stop(mp.video or mp.audio)  # noqa: best-effort
                    except Exception:
                        pass
            await on_close()

    from aiohttp import WSMsgType
    async for msg in ws:
        if msg.type != WSMsgType.TEXT:
            break
        data = json.loads(msg.data)
        if data.get('type') == 'offer':
            await pc.setRemoteDescription(RTCSessionDescription(
                sdp=data['sdp'], type='offer'))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
            # aiortc gathers ICE before resolving localDescription
            await ws.send_json({'type': 'answer',
                                'sdp': pc.localDescription.sdp})
        elif data.get('type') == 'bye':
            break
    await close()
