#!/usr/bin/env python3
"""End-to-end proof that the WebRTC stream tier works.

Drives real Chromium over CDP: it generates a genuine SDP offer, POSTs it to the
HTTP signaling endpoint, and then checks that decoded video frames actually
arrive. That last check is the one that matters — it exercises Xvfb, RetroArch,
x11grab, the H.264 encoder and the peer connection together, which is the whole
chain a console would use.

The page is loaded from the stream server itself so the fetch is same-origin and
plain http; an https page cannot POST to a LAN http server (mixed content), which
is exactly why the shell routes through its native host instead.

Usage: python3 verify_stream.py <platform> <rom-name> [base-url]
"""
import glob
import json
import os
import subprocess
import sys
import time

import requests
import websocket


def emulator_procs():
    """(pid, cmdline) for every running RetroArch."""
    out = []
    for path in glob.glob('/proc/[0-9]*/cmdline'):
        try:
            with open(path, 'rb') as f:
                argv = f.read().split(b'\0')
        except OSError:
            continue
        if argv and argv[0].endswith(b'retroarch'):
            out.append((path.split('/')[2],
                        b' '.join(a for a in argv if a).decode('utf8', 'replace')))
    return out


def open_input_nodes(pid):
    """Input device nodes this process has open."""
    found = []
    for fd in glob.glob(f'/proc/{pid}/fd/*'):
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if '/input/' in target:
            found.append(target)
    return found

platform = sys.argv[1] if len(sys.argv) > 1 else 'vectrex'
rom = sys.argv[2] if len(sys.argv) > 2 else None
BASE = sys.argv[3] if len(sys.argv) > 3 else 'http://127.0.0.1:8090'
PORT = 9340

passed, failed = [], []


def check(label, ok, detail=''):
    line = f"{'PASS' if ok else 'FAIL'}  {label}" + (f'  [{detail}]' if detail else '')
    (passed if ok else failed).append(line)
    print(line, flush=True)
    return ok


if rom is None:
    print('need a rom name', file=sys.stderr)
    sys.exit(2)

proc = subprocess.Popen(
    ['/bin/chromium', '--headless=new', f'--remote-debugging-port={PORT}',
     '--remote-allow-origins=*', '--no-sandbox', '--disable-gpu',
     '--autoplay-policy=no-user-gesture-required',
     '--user-data-dir=/tmp/streamverify', 'about:blank'],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
session_id = None
try:
    for _ in range(40):
        try:
            tabs = requests.get(f'http://127.0.0.1:{PORT}/json', timeout=2).json()
            if tabs:
                break
        except Exception:
            time.sleep(0.5)
    else:
        raise SystemExit('chromium never came up')

    ws = websocket.create_connection(tabs[0]['webSocketDebuggerUrl'], timeout=180,
                                     max_size=32 * 1024 * 1024)
    n = [0]

    def send(method, params=None):
        n[0] += 1
        ws.send(json.dumps({'id': n[0], 'method': method, 'params': params or {}}))
        while True:
            m = json.loads(ws.recv())
            if m.get('id') == n[0]:
                return m

    def ev(expr, await_promise=False):
        r = send('Runtime.evaluate', {'expression': expr, 'returnByValue': True,
                                      'awaitPromise': await_promise})
        res = r.get('result', {})
        if 'exceptionDetails' in res:
            return 'EXC ' + str(res['exceptionDetails'].get('text'))
        return res.get('result', {}).get('value')

    send('Runtime.enable')
    send('Page.enable')
    # Same-origin, plain http — see the module docstring.
    send('Page.navigate', {'url': BASE + '/remote?app=romm'})
    time.sleep(2)

    ev('''
    window.__st = {state:'init', err:null, sid:null, w:0};
    (async () => {
      try {
        const v = document.createElement('video');
        v.autoplay = true; v.muted = true; v.playsInline = true;
        document.body.appendChild(v);
        window.__v = v;
        const pc = new RTCPeerConnection(
          {iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
        window.__pc = pc;
        pc.ontrack = e => { v.srcObject = e.streams[0]; };
        pc.onconnectionstatechange = () => { window.__st.state = pc.connectionState; };
        pc.createDataChannel('input', {ordered:true});
        pc.addTransceiver('video', {direction:'recvonly'});
        pc.addTransceiver('audio', {direction:'recvonly'});
        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);
        await new Promise(r => {
          if (pc.iceGatheringState === 'complete') return r();
          pc.onicegatheringstatechange = () =>
            pc.iceGatheringState === 'complete' && r();
          setTimeout(r, 3000);
        });
        const resp = await fetch(location.origin + '/api/rtc/offer', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify({platform: %s, rom_name: %s,
                                sdp: pc.localDescription.sdp})});
        if (!resp.ok) { window.__st.err = 'http ' + resp.status + ' ' +
                        (await resp.text()).slice(0,200); return; }
        const j = await resp.json();
        window.__st.sid = j.session_id || null;
        if (!j.sdp) { window.__st.err = 'no sdp in answer'; return; }
        await pc.setRemoteDescription({type:'answer', sdp:j.sdp});
        window.__st.state = 'answered';
      } catch (e) { window.__st.err = String(e); }
    })();
    ''' % (json.dumps(platform), json.dumps(rom)))

    # Signaling
    deadline = time.time() + 90
    while time.time() < deadline:
        if ev('__st.sid') or ev('__st.err'):
            break
        time.sleep(1)
    err = ev('__st.err')
    session_id = ev('__st.sid')
    check('signaling returned an answer and a session', bool(session_id) and not err,
          str(err or session_id))

    if session_id:
        # The real test: decoded frames arriving means the entire capture and
        # encode chain behind the emulator is alive.
        deadline = time.time() + 120
        w = 0
        while time.time() < deadline:
            w = ev('__v && __v.videoWidth || 0') or 0
            if isinstance(w, int) and w > 0:
                break
            time.sleep(2)
        check('video frames decode on the client', isinstance(w, int) and w > 0,
              f'{w}x{ev("__v && __v.videoHeight || 0")}')

        # Frames arriving proves the capture chain, NOT that a game is running.
        # x11grab streams an empty Xvfb display at a healthy 30 fps, so this suite
        # once passed every check with no emulator process alive at all. Assert the
        # emulator itself.
        procs = emulator_procs()
        check('the emulator is actually running', bool(procs),
              procs[0][1][:90] if procs else 'no retroarch process exists')
        if procs:
            pid = procs[0][0]
            nodes = open_input_nodes(pid)
            # Not yet a failure: RetroArch here cannot load a config, so its
            # joypad driver cannot be switched to one that sees a virtual pad.
            # Buttons still work through key injection; analog does not.
            print(f"{'PASS' if nodes else 'INFO'}  emulator holds an input device"
                  f"  [{', '.join(nodes) if nodes else 'none — analog input unavailable'}]",
                  flush=True)
        check('peer connection reached a live state',
              ev('__st.state') in ('connected', 'answered'), str(ev('__st.state')))

        STATS = '''(async()=>{const s=await __pc.getStats();let out={};
          s.forEach(r=>{if(r.type==="inbound-rtp"&&r.kind==="video")
            out={frames:r.framesDecoded||0,bytes:r.bytesReceived||0};});
          return JSON.stringify(out);})()'''
        stats = ev(STATS, await_promise=True)
        check('frames decoded according to getStats',
              bool(stats) and '"frames"' in str(stats) and '"frames":0' not in str(stats),
              str(stats))

        # Sustained frame rate is the number that decides whether a platform is
        # actually playable or merely connects. Software rendering on a CPU with
        # no usable GPU streams the heavy consoles at single digits.
        try:
            f0 = json.loads(stats).get('frames', 0)
            time.sleep(15)
            f1 = json.loads(ev(STATS, await_promise=True)).get('frames', 0)
            fps = (f1 - f0) / 15.0
            check(f'sustained frame rate is playable ({fps:.1f} fps)', fps >= 20,
                  f'{f1 - f0} frames in 15s — under 20 fps is not playable')
        except Exception as e:
            check('sustained frame rate measured', False, str(e))

finally:
    if session_id:
        try:
            requests.post(f'{BASE}/api/rtc/{session_id}/stop', timeout=10)
            print(f'stopped session {session_id}', flush=True)
        except Exception as e:
            print(f'stop failed: {e}', flush=True)
    try:
        ws.close()
    except Exception:
        pass
    proc.terminate()

print()
print(f'==== {len(passed)} passed, {len(failed)} failed ====')
sys.exit(1 if failed else 0)
